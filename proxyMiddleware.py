from enum import Enum
from io import BytesIO
import itertools
from pprint import pformat
import logging
import dns.resolver
from flask import Flask, Request
import http.client
import re

BEHAVIOUR = Enum('BEHAVIOUR', [
    'REMOTE_FIRST',  # If remote and local differs return remote
    'LOCAL_FIRST',  # If remote and local differs return local
    'REMOTE_IF_MISSING',  # If local don't exist return remote otherwise return act as LOCAL_FIRST
    'ONLY_REMOTE',  # Ignore local
    'ONLY_LOCAL'  # Ingore remote
])

""" Standard Behaviour is REMOTE_IF_MISSING """
PROXY_URL_BEHAVIOUR = {
    r"/api/v1\.0/devices.*": BEHAVIOUR.ONLY_LOCAL,
    r"/fwUpgrade/PR06549/version\.txt": BEHAVIOUR.LOCAL_FIRST,
    r"/WifiBoxInterface_vokera/getWebTemperature\.php": BEHAVIOUR.REMOTE_FIRST
}


class ProxyMiddleware(object):

    upstream_resolver = dns.resolver.Resolver()
    http_connection: dict[str, http.client.HTTPConnection] = {}

    def __init__(self, app: Flask, upstream: str):
        self._app = app.wsgi_app
        self.app = app
        self.upstream_resolver.nameservers = [upstream]
        logging.info(
            f"Upstream DNS Check: google.com = {pformat(next(self.upstream_resolver.query('google.com', "A").__iter__()).to_text())}"
        )
        # for answer in self.upstream_resolver.query('google.com', "A"):
        #    logging.info(answer.to_text())

    def __call__(self, env, resp):  # sourcery skip: identity-comprehension
        if env.get("HTTP_HOST") in ['127.0.0.1', 'localhost'] or re.match(r"\w+\-besim\w{0,1}", env.get("HTTP_HOST"), re.IGNORECASE) is not None:
            return self._app(env, lambda status, headers, *args: resp(status, headers, *args))
        elif env.get("HTTP_HOST") not in self.http_connection or self.http_connection[env.get("HTTP_HOST")] is None:
            ip = next(self.upstream_resolver.query(env.get("HTTP_HOST"), "A").__iter__()).to_text()
            logging.info(f"Upstream Connection for {env.get("HTTP_HOST")} is {pformat(ip)}:{env.get("SERVER_PORT")}")
            self.http_connection[env.get("HTTP_HOST")] = http.client.HTTPConnection(ip, int(env.get("SERVER_PORT")))
            self.http_connection[env.get("HTTP_HOST")].auto_open = True

        logging.debug(pformat(env))

        req: Request = env['werkzeug.request']
        logging.debug(pformat(("REQUEST", req.__dict__)))

        def check_behaviour(path: str) -> BEHAVIOUR:
            for reg, bev in PROXY_URL_BEHAVIOUR.items():
                logging.debug(pformat((reg, bev)))
                if re.match(reg, path, re.IGNORECASE) is not None:
                    return bev
            return BEHAVIOUR.REMOTE_IF_MISSING

        # behaviour = PROXY_URL_BEHAVIOUR[req.path] if req.path in PROXY_URL_BEHAVIOUR else BEHAVIOUR.REMOTE_IF_MISSING
        behaviour = check_behaviour(req.path)
        logging.debug(f"Behaviour: {behaviour}")

        def check_path_exists(path: str, method: str):
            try:
                adapter = self.app.create_url_adapter(request=req)
                adapter.match(path, method)
            except Exception:
                return False
            return True

        if behaviour == BEHAVIOUR.REMOTE_IF_MISSING and not check_path_exists(req.path, req.method):
            logging.warn(f"Method {req.method} {req.path} don't exist. Force ONLY_REMOTE")
            behaviour = BEHAVIOUR.ONLY_REMOTE
        elif behaviour == BEHAVIOUR.REMOTE_IF_MISSING:
            behaviour = BEHAVIOUR.LOCAL_FIRST

        length = int(env.get("CONTENT_LENGTH", "0"))
        body = BytesIO(env["wsgi.input"].read(length))
        env["wsgi.input"] = body
        proxy_body = body.read(length)
        body.seek(0)

        if behaviour != BEHAVIOUR.ONLY_LOCAL:
            logging.debug(pformat(("PROXY_CALL", env['REQUEST_METHOD'], env['REQUEST_URI'], proxy_body, {x: y for x, y in req.headers.to_wsgi_list()})))
            self.http_connection[env.get("HTTP_HOST")].connect()
            self.http_connection[env.get("HTTP_HOST")].request(env['REQUEST_METHOD'], env['REQUEST_URI'], proxy_body, {x: y for x, y in req.headers.to_wsgi_list()})

            resp_org = self.http_connection[env.get("HTTP_HOST")].getresponse()
            body_org = "".join([chr(b) for b in resp_org.read()])
            logging.debug(pformat(("PROXY_RESPONSE", resp_org.headers, body_org)))
        # env['PROXY_RESP'] = resp_org
        # env['PROXY_BODY'] = body_org

        def intercept_response(status, headers, *args):
            if behaviour in [BEHAVIOUR.REMOTE_FIRST, BEHAVIOUR.ONLY_REMOTE]:
                headers = resp_org.headers.items()
            resp_int = resp(status, headers, *args)

            def writer(data):
                logging.debug(("RESPONSE_BODY", data))
                return resp_int(data)    
            logging.debug(pformat(("RESPONSE", status, headers, args)))
            return writer

        iterable = self._app(env, intercept_response)
        (org, copy) = itertools.tee(iterable)

        body_api = "".join([b.decode("utf-8") for b in copy])

        logging.debug(("RESPONSE_ITERABLE", body_api))

        """Check Reponse on Request For Proxy"""
        logging.info(f"{env['REQUEST_METHOD']} {env['REQUEST_URI']} {behaviour.name}")
        if behaviour not in (BEHAVIOUR.ONLY_LOCAL, BEHAVIOUR.ONLY_REMOTE) and body_api != body_org:
            logging.warning(f"Response form original_server and API differs Cloud=\"{body_org}\" Local=\"{body_api}\"")
            logging.debug((('REQ', req.__dict__, body),
                          ('REQ_CLOUD', env['REQUEST_METHOD'], env['REQUEST_URI'], proxy_body, {x: y for x, y in req.headers.to_wsgi_list()}),
                          ('RESP_CLOUD', resp_org.headers.__dict__, body_org),
                          ('RESP', body_api)))

        return iter([body_org.encode()]) if behaviour in [BEHAVIOUR.REMOTE_FIRST, BEHAVIOUR.ONLY_REMOTE] else org
