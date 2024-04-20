import argparse
from fileinput import filename
import io
import logging
from pprint import pformat
from collections import Counter
from typing import Any
import coloredlogs
import os
from proxyUdpServer import ProxyUdpServer

from udpserver import UdpServer
from restapi import app
from database import Database
from proxyMiddleware import ProxyMiddleware
from urllib.parse import ParseResult, urlparse

from ha_mqtt_discoverable.climate import (
    Climate,
    ClimateInfo,
    ClimateSetting,
)


def mqtt_url(arg):  # -> Any | ParseResult:
    url: ParseResult = urlparse(arg)
    if all((url.scheme in ["mqtt", "mqtts"], url.netloc)):  # possibly other sections?
        return url  # return url in case you need the parsed object
    raise argparse.ArgumentTypeError("Invalid URL")


if __name__ == "__main__":

    # Get the arguments from the command-line except the filename
    ap = argparse.ArgumentParser()

    ap.add_argument(
        "-l",
        "--log_level",
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

    ap.add_argument(
        "-s",
        "--static_dir",
        required=False,
        default=os.path.join(os.curdir, "static"),
        type=str,
        help="The dir from where serve /static content",
    )

    ap.add_argument(
        "-t",
        "--template_dir",
        required=False,
        default=os.path.join(os.curdir, "templates"),
        type=str,
        help="The dir where find index.html template",
    )

    ap.add_argument(
        "-c",
        "--config_path",
        required=False,
        default="/tmp",
        type=str,
        help="The Path where store configuration and db",
    )

    ap.add_argument(
        "--devmode",
        action=argparse.BooleanOptionalAction,
        required=False,
        default=False,
        type=bool,
        help="Start the Server with autoreaload feature",
    )

    ap.add_argument(
        "--mqtt-url",
        required=False,
        default=False,
        type=mqtt_url,
        help="MQTT server url in format mqtt(s)://[<user>:<passwors>@]<server>:<port>",
    )

    ap.add_argument(
        "--datalog-udp-path",
        required=False,
        default=False,
        type=str,
        help="Dump file for all UDP comunication - only for develop",
    )

    ap.add_argument(
        "--datalog-tcp-path",
        required=False,
        default=False,
        type=str,
        help="Dump file for all TCP comunication - only for develop",
    )

    args: dict[str, Any] = vars(ap.parse_args())

    fmt = "[%(asctime)s %(filename)s->%(funcName)s():%(lineno)d] %(levelname)s: %(message)s"
    if args["log_level"] == "TRACE":
        args["log_level"] = logging.DEBUG
    logging.basicConfig(format=fmt, level=args["log_level"])
    coloredlogs.install(
        isatty=True,
        level=args["log_level"],
        # fmt="%(levelname)s %(asctime)s %(module)s[%(lineno)d] %(message)s",
        fmt=fmt,
        field_styles=dict(
            asctime=dict(color="green"),
            hostname=dict(color="magenta"),
            levelname=dict(color="black", bold=True),
            name=dict(color="blue"),
            programname=dict(color="cyan"),
            username=dict(color="yellow"),
            filename=dict(color="blue"),
            lineno=dict(color="green", bold=True),
        ),
        level_styles=coloredlogs.DEFAULT_LEVEL_STYLES,
    )

    database_name: str = os.getenv(
        "BESIM_DATABASE", os.path.join(args["config_path"], "besim.db")
    )
    database = Database(name=database_name)
    if not database.check_migrations():
        os.remove(database_name)
        database.create_tables()

    database.purge(365 * 2)  # @todo currently only purging old records at startup

    app.template_folder = args["template_dir"]
    app.static_folder = args["static_dir"]

    # MQTT connection and config
    if args["mqtt_url"]:
        mqtt_params: ParseResult = args["mqtt_url"]
        assert mqtt_params.hostname
        mqtt_settings = ClimateSetting.MQTT(
            host=mqtt_params.hostname,
            port=mqtt_params.port,
            username=mqtt_params.username,
            password=mqtt_params.password,
            use_tls=mqtt_params.scheme == "mqtts",
        )
        logging.debug(("MQTT Settings", pformat(mqtt_settings)))
    """    
    climate_info = ClimateInfo(
        name=request.param["name"], temperature_unit="C", preset_modes=["eco", "boost"]
    )
    settings = ClimateSetting(
        mqtt=mqtt_settings,
        entity=climate_info,
        manual_availability=request.param["manual_availability"],
        capability=request.param["capability"],
    )
    """
    # logging.info(pformat(app.config), app.static_folder, app.template_folder)

    # Datalog Files
    if args["datalog_udp_path"] is not None:
        datalog_udp: io.TextIOWrapper = open(args["datalog_udp_path"], "at")
        # datalog_udp.write('"DIRECTION","ADDRESS","HEX_DATA_DUMP"\r\n')
        # datalog_udp.flush()
        # os.fsync(datalog_udp)
    if args["datalog_tcp_path"] is not None:
        datalog_tcp: io.TextIOWrapper = open(args["datalog_tcp_path"], "at")

    if args["weather_location"] is not None:
        # logging.(pformat(args["weather_location"]))
        app.config["weather_location_latitude"] = args["weather_location"]
    else:
        app.config["weather_location_latitude"] = [None, None]

    # udpServer = UdpServer(("", 6199))
    if args["proxy_mode"] is not None:
        udpServer = ProxyUdpServer(
            ("", 6199),
            args["proxy_mode"],
            debugmode=args["devmode"],
            datalog=datalog_udp,
        )
    else:
        udpServer = UdpServer(("", 6199), datalog=datalog_udp)
    udpServer.start()
    app.config["udpServer"] = udpServer
    # app.config["SERVER_NAME"] = "api.besmart-home.com:80"
    logging.debug(app.url_map)

    host: str = os.getenv("FLASK_HOST", "0.0.0.0")
    port: str = os.getenv("FLASK_PORT", "80")
    debug = bool(os.getenv("FLASK_DEBUG", logging.DEBUG >= logging.root.level))

    if args["proxy_mode"] is not None:
        app.wsgi_app = ProxyMiddleware(
            app,
            args["proxy_mode"],
            datalog=datalog_tcp,
        )

    # app.logger.setLevel(logging.WARN)
    logging.getLogger("werkzeug").setLevel(logging.WARN)
    app.run(
        debug=debug,
        host=host,
        port=int(port),
        use_debugger=True,
        use_reloader=args["devmode"],
    )
