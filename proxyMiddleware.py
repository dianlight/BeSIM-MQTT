from enum import Enum
from io import BytesIO
import itertools
from pprint import pformat
import logging
from wsgiref.types import StartResponse, WSGIEnvironment
from werkzeug import datastructures
import dns.resolver
from flask import Flask, Request
import http.client
import re
import typing as t

BEHAVIOUR = Enum(
    "BEHAVIOUR",
    [
        "REMOTE_FIRST",  # If remote and local differs return remote
        "LOCAL_FIRST",  # If remote and local differs return local
        "REMOTE_IF_MISSING",  # If local don't exist return remote otherwise return act as LOCAL_FIRST
        "ONLY_REMOTE",  # Ignore local
        "ONLY_LOCAL",  # Ingore remote
    ],
)

""" Standard Behaviour is REMOTE_IF_MISSING """
PROXY_URL_BEHAVIOUR = {
    r"/api/v1\.0/devices.*": BEHAVIOUR.ONLY_LOCAL,
    r"/fwUpgrade/PR06549/version\.txt": BEHAVIOUR.LOCAL_FIRST,
    r"/WifiBoxInterface_vokera/getWebTemperature\.php": BEHAVIOUR.REMOTE_FIRST,
}


class ProxyMiddleware(object):

    upstream_resolver = dns.resolver.Resolver()
    http_connection: dict[str, http.client.HTTPConnection] = {}

    def __init__(self, app: Flask, upstream: str) -> None:
        self._app = app.wsgi_app
        self.app: Flask = app

        if app.config["weather_location_latitude"][0] is not None:
            PROXY_URL_BEHAVIOUR[r"/WifiBoxInterface_vokera/getWebTemperature\.php"]=BEHAVIOUR.LOCAL_FIRST

        self.upstream_resolver.nameservers = [upstream]
        logging.info(
            f"Upstream DNS Check: google.com = {pformat(next(self.upstream_resolver.query('google.com', 'A').__iter__()).to_text())}"  # type: ignore
        )
        # for answer in self.upstream_resolver.query('google.com', "A"):
        #    logging.info(answer.to_text())

    def __call__(
        self, env: WSGIEnvironment, resp: StartResponse
    ) -> t.Iterable[bytes]:  # sourcery skip: identity-comprehension
        http_host = env.get("HTTP_HOST")
        if http_host is None:
            raise ValueError("Internal server error. HTTP_HOST env is null!")
        if (
            re.match(
                r"((\w+\-besim\w{0,1})|(127\.\d\.\d\.\d)|(localhost.*))(:\d+){0,1}",
                http_host,
                re.IGNORECASE,
            )
            is not None
        ):
            return self._app(
                env, lambda status, headers, *args: resp(status, headers, *args)
            )
        elif (
            http_host not in self.http_connection
            or self.http_connection[env.get("HTTP_HOST", "")] is None
        ):
            ip = next(self.upstream_resolver.query(http_host, "A").__iter__()).to_text()  # type: ignore
            logging.info(
                f"Upstream Connection for {http_host} is {pformat(ip)}:{env.get('SERVER_PORT','80')}"
            )
            self.http_connection[http_host] = http.client.HTTPConnection(
                ip, int(env.get("SERVER_PORT", "80"))
            )
            self.http_connection[http_host].auto_open = True

        req_headers = datastructures.EnvironHeaders(env)
        #  logging.debug(pformat(req_headers))

        #  req: Request = env['werkzeug.request']
        #  logging.debug(pformat(("REQUEST", req.__dict__)))

        def check_behaviour(path: str) -> BEHAVIOUR:
            for reg, bev in PROXY_URL_BEHAVIOUR.items():
                logging.debug(pformat((reg, bev)))
                if re.match(reg, path, re.IGNORECASE) is not None:
                    return bev
            return BEHAVIOUR.REMOTE_IF_MISSING

        # behaviour = PROXY_URL_BEHAVIOUR[req.path] if req.path in PROXY_URL_BEHAVIOUR else BEHAVIOUR.REMOTE_IF_MISSING
        behaviour: BEHAVIOUR = check_behaviour(env["REQUEST_URI"])
        logging.debug(f"Behaviour: {behaviour}")

        def check_path_exists(path: str, method: str) -> bool:
            try:
                vreq = Request(env)
                adapter = self.app.create_url_adapter(request=vreq)
                if adapter is not None:
                    adapter.match(path, method)
                else:
                    return False
            except Exception:
                return False
            return True

        if behaviour == BEHAVIOUR.REMOTE_IF_MISSING and not check_path_exists(
            env["REQUEST_URI"], env["REQUEST_METHOD"]
        ):
            logging.warn(
                f"Method {env['REQUEST_METHOD']} {env['REQUEST_URI']} don't exist. Force ONLY_REMOTE"
            )
            behaviour = BEHAVIOUR.ONLY_REMOTE
        elif behaviour == BEHAVIOUR.REMOTE_IF_MISSING:
            behaviour = BEHAVIOUR.LOCAL_FIRST

        length = int(env.get("CONTENT_LENGTH", "0"))
        body = BytesIO(env["wsgi.input"].read(length))
        env["wsgi.input"] = body
        proxy_body: bytes = body.read(length)
        body.seek(0)

        if behaviour != BEHAVIOUR.ONLY_LOCAL:
            logging.debug(
                pformat(
                    (
                        "PROXY_CALL",
                        env["REQUEST_METHOD"],
                        env["REQUEST_URI"],
                        proxy_body,
                        {x: y for x, y in req_headers.items()},
                        # {x: y for x, y in req.headers.to_wsgi_list()}
                    )
                )
            )
            self.http_connection[http_host].connect()
            self.http_connection[http_host].request(
                env["REQUEST_METHOD"],
                env["REQUEST_URI"],
                proxy_body,
                {x: y for x, y in req_headers.items()},
                # {x: y for x, y in req.headers.to_wsgi_list()}
            )

            resp_org: http.client.HTTPResponse = self.http_connection[
                http_host
            ].getresponse()
            body_org: str = "".join([chr(b) for b in resp_org.read()])
            logging.debug(pformat(("PROXY_RESPONSE", resp_org.headers, body_org)))

        def intercept_response(status, headers, *args):  # -> Callable[..., object]:
            if behaviour in [BEHAVIOUR.REMOTE_FIRST, BEHAVIOUR.ONLY_REMOTE]:
                headers = resp_org.headers.items()
            resp_int = resp(status, headers, *args)

            def writer(data) -> object:
                logging.debug(("RESPONSE_BODY", data))
                return resp_int(data)

            logging.debug(pformat(("RESPONSE", status, headers, args)))
            return writer

        iterable: t.Iterable[bytes] = self._app(env, intercept_response)
        (org, copy) = itertools.tee(iterable)

        body_api: str = "".join([b.decode("utf-8") for b in copy])

        logging.debug(("RESPONSE_ITERABLE", body_api))

        """Check Reponse on Request For Proxy"""
        logging.info(f"{env['REQUEST_METHOD']} {env['REQUEST_URI']} {behaviour.name}")
        if (
            behaviour not in (BEHAVIOUR.ONLY_LOCAL, BEHAVIOUR.ONLY_REMOTE)
            and body_api != body_org
        ):
            logging.warning(
                f'Response form original_server and API differs Cloud="{body_org}" Local="{body_api}"'
            )
            logging.debug(
                (
                    ("REQ", req_headers.__dict__, body),
                    (
                        "REQ_CLOUD",
                        env["REQUEST_METHOD"],
                        env["REQUEST_URI"],
                        proxy_body,
                        {x: y for x, y in req_headers.items()},
                    ),
                    ("RESP_CLOUD", resp_org.headers.__dict__, body_org),
                    ("RESP", body_api),
                )
            )

        return (
            iter([body_org.encode()])
            if behaviour in [BEHAVIOUR.REMOTE_FIRST, BEHAVIOUR.ONLY_REMOTE]
            else org
        )
