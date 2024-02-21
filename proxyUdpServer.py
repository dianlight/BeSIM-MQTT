import logging
import threading
import time

# from pprint import pformat
import hexdump
import dns.resolver
from udpserver import (
    UNUSED_CSEQ,
    Frame,
    LastCSeq,
    MsgId,
    SignalCSeq,
    UdpServer,
    Unpacker,
    Wrapper,
)


class ProxyUdpServer(UdpServer):

    def __init__(self, addr, upstream: str):
        super().__init__(addr)
        upstream_resolver = dns.resolver.Resolver()
        upstream_resolver.nameservers = [upstream]
        upstream_ip = next(
            upstream_resolver.query("api.besmart-home.com", "A").__iter__()
        ).to_text()
        logging.info(f"Upstream DNS Check: api.besmart-home.com = {upstream_ip}")
        self.cloud_addr = (upstream_ip, 6199)

    def run(self):
        logging.info(f"Proxy UDP server is running to {self.cloud_addr}")
        super().run()

    def handleCloudMsg(self, data, addr):

        frame = Frame()
        payload = frame.decode(data)
        seq = frame.seq
        length = len(payload)

        # peerStatus = getPeerStatus(addr)
        # peerStatus["seq"] = seq  # @todo handle sequence number

        # Now handle the payload

        wrapper = Wrapper(from_cloud=True)
        payload = wrapper.decodeUL(payload)

        msgLen = len(payload)
        logging.info(f"Cloud: {seq=} {wrapper} {length=} {msgLen=}")

        unpack = Unpacker(payload)

        if wrapper.msgType == MsgId.STATUS:
            cseq, unk1, unk2, deviceid, lastseen = unpack("<BBHII")  # 1 1 2 4 4
            logging.info(
                f"Cloud {wrapper.msgType=:x} {cseq=:x} {unk1=:x} {unk2=:x} {deviceid=} {lastseen=}"
            )
        elif wrapper.msgType == MsgId.PROGRAM:
            """
            [2024-02-21 18:01:53,113 udpserver.py->run():449] INFO: From ('104.46.56.16', 6199) 54 bytes : FA D4 2A 00 FF FF FF FF 0A 0F 1E 00 FF 00 00 00 AA F2 8D 23 A6 27 43 04 06 00 00 00 00 00 00 00 11 21 22 11 11 11 11 11 11 11 11 11 11 11 11 11 11 00 63 D7 2D DF
            [2024-02-21 18:01:53,114 proxyUdpServer.py->handleCloudMsg():52] INFO: Cloud: seq=4294967295 msgType=10(a) synclost=0 downlink=1 response=1 write=1 flags=f length=42 msgLen=38
            [2024-02-21 18:01:53,115 proxyUdpServer.py->handleCloudMsg():465] WARNING: Cloud Unhandled message 10 len:msgLen=38
            """
            cseq, unk1, unk2, deviceid, room, day, *prog = unpack(
                "<BBHIIH24B"
            )  # 1 1 2 4 4 2
            logging.info(
                f"Cloud {wrapper.msgType=:x} {cseq=:x} {unk1=:x} {unk2=:x} {deviceid=} {room=} {day=} prog={ [ hex(l) for l in prog ] }"
            )
        elif wrapper.msgType == MsgId.PROG_END:
            """
            [2024-02-21 18:01:53,148 udpserver.py->run():449] INFO: From ('104.46.56.16', 6199) 30 bytes : FA D4 12 00 FF FF FF FF 2A 0F 06 00 FF 00 00 00 AA F2 8D 23 A6 27 43 04 14 0A D1 BF 2D DF
            [2024-02-21 18:01:53,149 proxyUdpServer.py->handleCloudMsg():52] INFO: Cloud: seq=4294967295 msgType=42(2a) synclost=0 downlink=1 response=1 write=1 flags=f length=18 msgLen=14
            [2024-02-21 18:01:53,150 proxyUdpServer.py->handleCloudMsg():465] WARNING: Cloud Unhandled message 42 len:msgLen=14
            """
            cseq, unk1, unk2, deviceid, room, unk3 = unpack("<BBHIIH")  # 1 1 2 4 4 2
            logging.info(
                f"Cloud {wrapper.msgType=:x} {cseq=:x} {unk1=:x} {unk2=:x} {deviceid=} {lastseen=}"
            )
            """
            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            rooms_to_get_prog = (
                set()
            )  # Set of rooms for which we need to get the current program

            for n in range(8):  # Supports up to 8 thermostats
                room, byte1, byte2, temp, settemp, t3, t2, t1, maxsetp, minsetp = (
                    unpack("<IBBhhhhhhh")
                )

                mode = byte2 >> 4
                unk9 = byte2 & 0xF
                byte3, byte4, unk13, tempcurve, heatingsetp = unpack("<BBHBB")
                sensorinfluence = (byte3 >> 3) & 0xF
                units = (byte3 >> 2) & 0x1
                advance = (byte3 >> 1) & 0x1
                boost = (byte4 >> 2) & 0x1
                cmdissued = (byte4 >> 1) & 0x1
                winter = byte4 & 0x1

                # Assume that if room is zero, 0xffffffff or byte1 is zero, then no thermostat is connected for that room
                if room != 0 and room != 0xFFFFFFFF and byte1 != 0:
                    logging.info(
                        f"{room=:x} {byte1=:x} {mode=} {temp=} {settemp=} {t3=} {t2=} {t1=} {maxsetp=} {minsetp=} {sensorinfluence=} {units=} {advance=} {boost=} {cmdissued=} {winter=} {tempcurve=} {heatingsetp=}"
                    )
                    if byte1 == 0x8F:
                        heating = 1
                    elif byte1 == 0x83:
                        heating = 0
                    else:
                        logging.warn(f"Unexpected {byte1=:x}")
                        heating = None

                    roomStatus = getRoomStatus(deviceid, room)

                    roomStatus["heating"] = heating
                    roomStatus["temp"] = temp
                    roomStatus["settemp"] = settemp
                    roomStatus["t3"] = t3
                    roomStatus["t2"] = t2
                    roomStatus["t1"] = t1
                    roomStatus["maxsetp"] = maxsetp
                    roomStatus["minsetp"] = maxsetp
                    roomStatus["mode"] = mode
                    roomStatus["tempcurve"] = tempcurve
                    roomStatus["heatingsetp"] = heatingsetp
                    roomStatus["sensorinfluence"] = sensorinfluence
                    roomStatus["units"] = units
                    roomStatus["advance"] = advance
                    roomStatus["boost"] = boost
                    roomStatus["cmdissued"] = cmdissued
                    roomStatus["winter"] = winter

                    roomStatus["lastseen"] = int(time.time())

                    if self.db is not None:
                        # @todo log other parameters..
                        self.db.log_temperature(
                            room, temp / 10.0, settemp / 10.0, heating, conn=self.dbConn
                        )
                        self.dbConn.commit()

                    if len(roomStatus["days"]) != 7 or wrapper.cloudsynclost:
                        rooms_to_get_prog.add(room)

                    # Handle fake boost timer
                    if "fakeboost" in roomStatus:
                        if (
                            roomStatus["fakeboost"] != 0
                            and roomStatus["fakeboost"] < time.time()
                        ):
                            # Call send_FAKE_BOOST but this needs to be done from a new thread
                            # because it is blocking.
                            # self.send_FAKE_BOOST(addr,deviceStatus,deviceid,room,0)
                            thread = threading.Thread(
                                target=self.send_FAKE_BOOST,
                                args=(addr, deviceStatus, deviceid, room, 0),
                            )
                            thread.start()
                    else:
                        roomStatus["fakeboost"] = 0

            # OpenTherm parameters
            # From the manual we expect the following to be present somewhere:
            # tSEt = set-point flow temperature calculated by the thermostat.
            # tFLO = reading of the boiler flow sensor temperature.
            # trEt = reading of the boiler return sensor temperature.
            # tdH = reading of the boiler DHW sensor temperature.
            # tFLU = reading of the boiler flues sensor temperature.
            # tESt = reading of the boiler outdoor sensor temperature (fitted to the boiler or
            # communicated by the web).
            # MOdU = instantaneous percentage of modulation of boiler fan.
            # FLOr = instantaneous domestic hot water flow rate.
            # HOUr = hours worked in high condensation mode.
            # PrES = central heating system pressure.
            # tFL2 = reading of the heating flow sensor on second circuit

            otFlags1, otFlags2 = unpack("<BB")

            boilerHeating = (otFlags1 >> 5) & 0x1
            dhwMode = (otFlags1 >> 6) & 0x1

            deviceStatus["boilerOn"] = boilerHeating
            deviceStatus["dhwMode"] = dhwMode

            otUnk1, otUnk2, tFLO, otUnk4, tdH, tESt, otUnk7, otUnk8, otUnk9, otUnk10 = (
                unpack("<hhhhhhhhhh")
            )

            deviceStatus["tFLO"] = tFLO
            deviceStatus["tdH"] = tdH
            deviceStatus["tESt"] = tESt

            # Other params

            wifisignal, unk16, unk17, unk18, unk19, unk20 = unpack("<BBHHHH")

            deviceStatus["wifisignal"] = wifisignal
            deviceStatus["lastseen"] = int(time.time())

            logging.info(getStatus())

            # Send a DL STATUS message
            self.send_STATUS(addr, deviceid, deviceStatus["lastseen"], response=1)
        elif wrapper.msgType == MsgId.GET_PROG:
            cseq, unk1, unk2, deviceid, room, unk3 = unpack("<BBHIII")

            logging.info(f"{deviceid=} {room=}")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            if cseq != LastCSeq(deviceStatus):
                logging.warn(f"Unexpected {cseq=:x}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 1:
                logging.warn(f"Unexpected {unk2=:x}")

            if unk3 != 0x800FE0:
                logging.warn(f"Unexpected {unk3=:x}")

            if wrapper.response:
                SignalCSeq(
                    deviceStatus, cseq, unk3
                )  # @todo Is there any meaningful data in the response?

        elif wrapper.msgType == MsgId.PING:
            cseq, unk1, unk2, deviceid, unk3 = unpack("<BBHIH")

            logging.info(f"{deviceid=}")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            if cseq != UNUSED_CSEQ:
                logging.warn(f"Unexpected {cseq=}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            # on uplink unk2 is usually 4, but can be zero (when out of sync?)
            if unk2 != 4 and unk2 != 0:
                logging.warn(f"Unexpected {unk2=:x}")

            if unk3 != 1:
                logging.warn(f"Unexpected {unk3=:x}")

            # Send a DL PING message
            self.send_PING(addr, deviceid, response=1)

        elif wrapper.msgType == MsgId.REFRESH:
            cseq, unk1, unk2, deviceid = unpack("<BBHI")
            # Padding at end ??
            logging.info(f"{deviceid=}")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            if cseq != LastCSeq(deviceStatus):
                logging.warn(f"Unexpected {cseq}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 0x1:
                logging.warn(f"Unexpected {unk2=:x}")

            if wrapper.response:
                SignalCSeq(
                    deviceStatus, cseq, unk2
                )  # @todo Is there any meaninngful data in the response?

        elif wrapper.msgType == MsgId.DEVICE_TIME:
            # It looks like only the 1st byte in DEVICE_TIME is valid
            # 0 = no dst 1 = dst ?
            # The rest of the payload appears to be garbage?
            cseq, unk1, unk2, deviceid, val, unk3, unk4, unk5 = unpack("<BBHIBBHI")
            logging.info(f"{deviceid=} {val=}")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            if cseq != LastCSeq(deviceStatus):
                logging.warn(f"Unexpected {cseq=}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 0x1:
                logging.warn(f"Unexpected {unk2=:x}")

            if unk3 != 0x0:
                logging.warn(f"Unexpected {unk3=:x}")

            if unk4 != 0x0:
                logging.warn(f"Unexpected {unk4=:x}")

            if unk5 != 0x0:
                logging.warn(f"Unexpected {unk5=:x}")

            if wrapper.response:
                SignalCSeq(deviceStatus, cseq, val)

        elif wrapper.msgType == MsgId.OUTSIDE_TEMP:
            cseq, unk1, unk2, deviceid, val = unpack("<BBHIB")

            logging.info(f"{deviceid=} {val=}")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            if cseq != LastCSeq(deviceStatus):
                logging.warn(f"Unexpected {cseq=}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 0x1:
                logging.warn(f"Unexpected {unk2=:x}")

            # val  = 0x0 means no external temperature management
            #        0x1 means boiler external temperature management
            #      = 0x2 means web external temperature management

            if wrapper.response:
                SignalCSeq(deviceStatus, cseq, val)

        elif wrapper.msgType == MsgId.PROG_END:
            cseq, unk1, unk2, deviceid, room, unk3 = unpack("<BBHIIH")
            logging.info(f"{deviceid=} {room=} {unk3=:x}")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            if cseq != UNUSED_CSEQ:
                logging.warn(f"Unexpected {cseq=}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 0x1:
                logging.warn(f"Unexpected {unk2=:x}")

            if unk3 != 0xA14:
                logging.warn(f"Unexpected {unk3=:x}")

            # Send a PROG_END
            if wrapper.response != 1:
                self.send_PROG_END(addr, deviceid, room, response=1)

        elif wrapper.msgType == MsgId.SWVERSION:
            cseq, unk1, unk2, deviceid, version = unpack("<BBHI13s")
            logging.info(f"{deviceid=} {version=}")
            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            deviceStatus["version"] = str(version)

            if cseq != LastCSeq(deviceStatus):
                logging.warn(f"Unexpected {cseq=}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 1:
                logging.warn(f"Unexpected {unk2=:x}")

            if wrapper.response != 1:
                self.send_SWVERSION(addr, deviceStatus, deviceid, response=1)
            else:
                SignalCSeq(deviceStatus, cseq, str(version))

        elif wrapper.msgType == MsgId.PROGRAM:
            cseq, unk1, unk2, deviceid, room, day = unpack("<BBHIIH")
            prog = []
            for i in range(24):
                (p,) = unpack("<B")
                prog.append(p)
            logging.info(f"{deviceid=} {room=} {day=} prog={ [ hex(l) for l in prog ] }")

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            roomStatus = getRoomStatus(deviceid, room)
            roomStatus["days"][day] = prog
            logging.info(getStatus())

            if cseq != UNUSED_CSEQ:
                logging.warn(f"Unexpected {cseq=}")

            if unk1 != 0x2:
                logging.warn(f"Unexpected {unk1=:x}")

            if unk2 != 1:
                logging.warn(f"Unexpected {unk2=:x}")

            # Send a DL PROGRAM message
            if wrapper.response != 1:
                self.send_PROGRAM(
                    addr, deviceStatus, deviceid, room, day, prog, response=1
                )

        elif self.set_messages_payload_size(wrapper.msgType) is not None:
            # Handles generic MsgId.SET_* messages
            # @todo can any of the MsgId.SET_* values be negative?
            cseq, flags, unk2, deviceid, room = unpack("<BBHII")

            numBytes = self.set_messages_payload_size(wrapper.msgType)

            if numBytes == 4:
                (value,) = unpack("<I")
            elif numBytes == 2:
                (value,) = unpack("<H")
            elif numBytes == 1:
                (value,) = unpack("<B")
            else:
                logging.warn(f"Unrecognised MsgType {wrapper.msgType:x}")
                value = None

            deviceStatus = getDeviceStatus(deviceid)
            peerStatus["devices"].add(deviceid)
            deviceStatus["addr"] = addr

            roomStatus = getRoomStatus(deviceid, room)

            logging.info(f"{cseq=} {deviceid=} {room=} {value=}")

            # Update the device status with the updated value
            if wrapper.msgType == MsgId.SET_T1:
                roomStatus["t1"] = value
            elif wrapper.msgType == MsgId.SET_T2:
                roomStatus["t2"] = value
            elif wrapper.msgType == MsgId.SET_T3:
                roomStatus["t3"] = value
            elif wrapper.msgType == MsgId.SET_MIN_HEAT_SETP:
                roomStatus["minsetp"] = value
            elif wrapper.msgType == MsgId.SET_MAX_HEAT_SETP:
                roomStatus["maxsetp"] = value
            elif wrapper.msgType == MsgId.SET_UNITS:
                roomStatus["units"] = value
            elif wrapper.msgType == MsgId.SET_SEASON:
                roomStatus["winter"] = value
            elif wrapper.msgType == MsgId.SET_ADVANCE:
                roomStatus["advance"] = value
            elif wrapper.msgType == MsgId.SET_MODE:
                roomStatus["mode"] = value
            elif wrapper.msgType == MsgId.SET_SENSOR_INFLUENCE:
                roomStatus["sensorinfluence"] = value
            elif wrapper.msgType == MsgId.SET_CURVE:
                roomStatus["tempcurve"] = value

            if unk2 != 0x1:
                logging.warn(f"Unexpected {unk2=:x}")

            if wrapper.downlink and flags != 0x0:
                logging.warn(f"Unexpected {flags=:x} for downlink")

            if not wrapper.downlink and (flags != 0x0 and flags != 0x2):
                logging.warn(f"Unexpected {flags=:x} for uplink")

            # Send a DL SET message if this was initiated by the device
            if value is not None:
                if wrapper.response != 1:
                    self.send_SET(
                        addr, device, deviceid, room, wrapper.msgType, value, response=1
                    )
                else:
                    SignalCSeq(deviceStatus, cseq, value)
        """
        else:
            logging.warn(f"Cloud Unhandled message {wrapper.msgType} len:{msgLen=}")
            unpack.setOffset(msgLen)  # To skip false inernal error

        if unpack.getOffset() != msgLen:
            # Check we have consumed the complete message we received
            logging.warn(f"Cloud Internal error offset={unpack.getOffset()} {msgLen=}")

    def handleMsg(self, data, addr):
        if addr == self.cloud_addr:
            return self.handleCloudMsg(data, addr)
        logging.debug(
            f"Cloud replicate message {len(data)} bytes : {hexdump.dump(data)} to {self.cloud_addr}"
        )
        self.sock.sendto(data, self.cloud_addr)
        return super().handleMsg(data, addr)
