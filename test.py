import time
from dataclasses import dataclass

import serial


@dataclass
class SerialProfile:
    baudrate: int
    parity: str
    stopbits: float

    def label(self) -> str:
        parity_map = {
            serial.PARITY_NONE: "N",
            serial.PARITY_EVEN: "E",
            serial.PARITY_ODD: "O",
        }
        sb = "1" if self.stopbits == serial.STOPBITS_ONE else "2"
        return f"{self.baudrate}-{parity_map.get(self.parity, '?')}{sb}"


def modbus_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_modbus_rtu_request(slave_id: int, start_addr: int, quantity: int) -> bytes:
    pdu = bytes(
        [
            slave_id & 0xFF,
            0x03,
            (start_addr >> 8) & 0xFF,
            start_addr & 0xFF,
            (quantity >> 8) & 0xFF,
            quantity & 0xFF,
        ]
    )
    crc = modbus_crc16(pdu)
    return pdu + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def is_valid_crc(frame: bytes) -> bool:
    if len(frame) < 5:
        return False
    crc = modbus_crc16(frame[:-2])
    return frame[-2] == (crc & 0xFF) and frame[-1] == ((crc >> 8) & 0xFF)


def parse_valid_rtu_frames(blob: bytes) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    i = 0
    n = len(blob)
    while i <= n - 5:
        addr = blob[i]
        if not (1 <= addr <= 247):
            i += 1
            continue

        func = blob[i + 1]
        candidate_lengths: list[int] = []

        if func & 0x80:
            candidate_lengths = [5]
        elif func in (0x03, 0x04):
            if i + 2 < n:
                byte_count = blob[i + 2]
                candidate_lengths = [5 + byte_count]
        elif func in (0x05, 0x06, 0x0F, 0x10):
            candidate_lengths = [8]
        else:
            candidate_lengths = list(range(5, 16))

        matched = False
        for frame_len in candidate_lengths:
            if i + frame_len > n:
                continue
            frame = blob[i : i + frame_len]
            if is_valid_crc(frame):
                frames.append((i, frame))
                i += frame_len
                matched = True
                break

        if not matched:
            i += 1

    return frames


def collect_raw(ser: serial.Serial, seconds: float) -> bytes:
    end = time.monotonic() + seconds
    data = bytearray()
    while time.monotonic() < end:
        chunk = ser.read(ser.in_waiting or 1)
        if chunk:
            data.extend(chunk)
        else:
            time.sleep(0.005)
    return bytes(data)


def decode_parity_name(parity: str) -> str:
    if parity == serial.PARITY_NONE:
        return "None"
    if parity == serial.PARITY_EVEN:
        return "Even"
    if parity == serial.PARITY_ODD:
        return "Odd"
    return parity


def main() -> None:
    port = "/dev/ttyAMA3"
    timeout = 0.05

    start_addr = 0x0003
    quantity = 2
    target_ids = [1, 2, 3]
    rounds_per_id = 3
    passive_listen_seconds = 1.5
    response_window_seconds = 0.35

    profiles = [
        SerialProfile(9600, serial.PARITY_NONE, serial.STOPBITS_ONE),
        SerialProfile(9600, serial.PARITY_EVEN, serial.STOPBITS_ONE),
        SerialProfile(19200, serial.PARITY_NONE, serial.STOPBITS_ONE),
        SerialProfile(19200, serial.PARITY_EVEN, serial.STOPBITS_ONE),
        SerialProfile(115200, serial.PARITY_NONE, serial.STOPBITS_ONE),
        SerialProfile(115200, serial.PARITY_EVEN, serial.STOPBITS_ONE),
    ]

    print(f"串口诊断开始: port={port}")
    print(
        f"轮询寄存器: func=03, start=0x{start_addr:04X}, qty={quantity}, IDs={target_ids}, 每ID轮询{rounds_per_id}次"
    )
    print("=" * 72)

    summary: list[dict] = []

    for profile in profiles:
        print(
            f"\n[串口参数] {profile.label()}  (parity={decode_parity_name(profile.parity)}, stopbits={profile.stopbits})"
        )
        try:
            with serial.Serial(
                port=port,
                baudrate=profile.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=profile.parity,
                stopbits=profile.stopbits,
                timeout=timeout,
            ) as ser:
                ser.reset_input_buffer()
                passive_raw = collect_raw(ser, passive_listen_seconds)
                passive_frames = parse_valid_rtu_frames(passive_raw)
                print(
                    f"被动监听{passive_listen_seconds:.1f}s: 原始{len(passive_raw)}字节, CRC有效帧{len(passive_frames)}"
                )

                if passive_frames:
                    print("  警告: 未发送请求时已存在总线流量，可能有其他主站或现场周期通信。")

                id_result: dict[int, dict] = {}

                for sid in target_ids:
                    total_raw = bytearray()
                    valid_frames = 0
                    valid_match_frames = 0
                    reply_payloads: set[bytes] = set()

                    for _ in range(rounds_per_id):
                        req = build_modbus_rtu_request(sid, start_addr, quantity)
                        ser.reset_input_buffer()
                        ser.write(req)
                        ser.flush()

                        raw = collect_raw(ser, response_window_seconds)
                        total_raw.extend(raw)
                        frames = parse_valid_rtu_frames(raw)

                        for _, frame in frames:
                            valid_frames += 1
                            if frame[0] == sid:
                                valid_match_frames += 1
                                if frame[1] == 0x03 and len(frame) >= 9:
                                    reply_payloads.add(frame[3:-2])

                    id_result[sid] = {
                        "raw_len": len(total_raw),
                        "valid_frames": valid_frames,
                        "valid_match_frames": valid_match_frames,
                        "payload_count": len(reply_payloads),
                    }

                    print(
                        f"  ID={sid}: 原始{len(total_raw)}字节, CRC有效帧{valid_frames}, 其中地址匹配{valid_match_frames}, 有效载荷种类{len(reply_payloads)}"
                    )

                summary.append(
                    {
                        "profile": profile.label(),
                        "passive_frames": len(passive_frames),
                        "id_result": id_result,
                    }
                )
        except Exception as e:
            print(f"  打开串口失败: {e}")
            summary.append(
                {
                    "profile": profile.label(),
                    "passive_frames": 0,
                    "id_result": {},
                    "error": str(e),
                }
            )

    print("\n" + "=" * 72)
    print("结论建议")

    usable_profiles = [s for s in summary if s.get("id_result")]
    if not usable_profiles:
        print("未获得可用采样，请先确认串口权限、接线和设备上电。")
        return

    only_id1_profiles = 0
    interference_profiles = 0

    for item in usable_profiles:
        if item["passive_frames"] > 0:
            interference_profiles += 1

        id_result = item["id_result"]
        id1_match = id_result.get(1, {}).get("valid_match_frames", 0)
        other_match = sum(
            v.get("valid_match_frames", 0) for k, v in id_result.items() if k != 1
        )
        if id1_match > 0 and other_match == 0:
            only_id1_profiles += 1

    print(f"仅ID=1有匹配应答的参数组: {only_id1_profiles}/{len(usable_profiles)}")
    print(f"检测到被动总线流量的参数组: {interference_profiles}/{len(usable_profiles)}")

    if only_id1_profiles >= 1 and interference_profiles == 0:
        print("判断: 高概率是现场设备SlaveID未区分(大量设备仍为默认ID=1)。")
    elif only_id1_profiles >= 1 and interference_profiles > 0:
        print("判断: 现象支持“ID=1集中应答”，但总线上存在外部流量干扰，结论仍需隔离现场主站后二次确认。")
    else:
        print("判断: 目前数据不足以锁定为SlaveID问题，请优先确认串口参数与接线，再扩大ID扫描范围。")


if __name__ == "__main__":
    main()
