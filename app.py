import argparse
from ast import arg
import logging
from pprint import pformat
from collections import Counter
from typing import Any
import coloredlogs
import os
import sys
from proxyUdpServer import ProxyUdpServer

from udpserver import UdpServer
from restapi import app
from database import Database
from proxyMiddleware import ProxyMiddleware


if __name__ == "__main__":

    # Get the arguments from the command-line except the filename
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "-l",
        "--logLevel",
        required=False,
        default=logging.INFO,
        choices=dict(
            Counter(logging.getLevelNamesMapping().keys()) + Counter({"TRACE"})
        ),
    )

    ap.add_argument(
        "-p",
        "--proxy_mode",
        required=False,
        default="1.1.1.1",
        help="the IP of upstream DNS to resolve hosts",
    )

    ap.add_argument(
        "-w",
        "--weather_location",
        action="extend",
        nargs=2,
        required=False,
        type=float,
        help="Uses met.no to get the weather at the servers' latitude, longitude",
    )

    args: dict[str, Any] = vars(ap.parse_args())

    fmt = "[%(asctime)s %(filename)s->%(funcName)s():%(lineno)s] %(levelname)s: %(message)s"
    if args["logLevel"] == "TRACE":
        args["logLevel"] = logging.DEBUG
    logging.basicConfig(format=fmt, level=args["logLevel"])
    coloredlogs.install(isatty=True, level=args["logLevel"])

    database_name = os.getenv("BESIM_DATABASE", "besim.db")
    database = Database(name=database_name)
    if not database.check_migrations():
        sys.exit(1)  # error should already have been logged
    database.purge(365 * 2)  # @todo currently only purging old records at startup

    if args["weather_location"] is not None:
        # logging.(pformat(args["weather_location"]))
        app.config["weather_location_latitude"] = args["weather_location"]
    else:
        app.config["weather_location_latitude"] = [None, None]

    # udpServer = UdpServer(("", 6199))
    if args["proxy_mode"] is not None:
        udpServer = ProxyUdpServer(("", 6199), args["proxy_mode"])
    else:
        udpServer = UdpServer(("", 6199))
    udpServer.start()
    app.config["udpServer"] = udpServer
    # app.config["SERVER_NAME"] = "api.besmart-home.com:80"
    logging.debug(app.url_map)

    host: str = os.getenv("FLASK_HOST", "0.0.0.0")
    port: str = os.getenv("FLASK_PORT", "80")
    debug = bool(os.getenv("FLASK_DEBUG", logging.DEBUG >= logging.root.level))

    if args["proxy_mode"] is not None:
        app.wsgi_app = ProxyMiddleware(app, args["proxy_mode"])

    # app.logger.setLevel(logging.WARN)
    logging.getLogger("werkzeug").setLevel(logging.WARN)
    app.run(
        debug=debug, host=host, port=int(port), use_debugger=True, use_reloader=False
    )
