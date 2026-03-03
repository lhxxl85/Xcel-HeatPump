#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import curses
from queue import Empty, Queue
import signal
from threading import Lock
import time
from dataclasses import dataclass
import unicodedata
from typing import Dict, Iterable, List, Mapping, Tuple

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

from src.hp_controller.master import register_mapping
from src.hp_controller.master.register_mapping import HP_REGISTER_MAP


SLAVE_LAYOUT: Dict[str, Tuple[int, ...]] = {
    "AMA5": (1, 3, 4),
    "AMA3": (5, 6, 7),
}
SLAVE_IDS: Tuple[int, ...] = (1, 3, 4, 5, 6, 7)

REGISTER_BLOCKS: Tuple[Tuple[int, int], ...] = (
    (0x0000, 50),
    (0x006D, 50),
)

REGISTER_ORDER: List[Tuple[int, str]] = sorted(HP_REGISTER_MAP.items(), key=lambda x: x[0])
BIT_TRANSLATE: Mapping[int, Mapping[int, str]] = getattr(
    register_mapping,
    "bit_tranlte",
    getattr(register_mapping, "BIT_Translate", {}),
)
RW_BIT_REGISTER_ADDRS: Tuple[int, ...] = tuple(addr for addr in (0x0000, 0x0001) if addr in BIT_TRANSLATE)
RO_BIT_REGISTER_ADDRS: Tuple[int, ...] = tuple(sorted(addr for addr in BIT_TRANSLATE if addr >= 0x006D))
BIT_TRANSLATE_ADDRS = set(RW_BIT_REGISTER_ADDRS) | set(RO_BIT_REGISTER_ADDRS)
NORMAL_REGISTER_ORDER: List[Tuple[int, str]] = [
    item for item in REGISTER_ORDER if item[0] not in BIT_TRANSLATE_ADDRS
]

NAME_COLOR_PAIR = 1
VALUE_COLOR_PAIR = 2
RO_VALUE_COLOR_PAIR = 3
REGISTER_ITEMS_PER_ROW = 3


@dataclass
class SerialConfig:
    port: str
    baudrate: int = 9600
    parity: str = "N"
    stopbits: int = 1
    bytesize: int = 8
    timeout_sec: float = 2.0


@dataclass
class SlaveState:
    slave_id: int
    bus_name: str
    registers: Dict[str, int | float]
    ok: bool
    message: str
    last_read: float | None


@dataclass
class ControlState:
    address_setting: str = ""
    value_setting: str = ""
    active_index: int = 0
    selected_slave_index: int = 0
    status_message: str = "等待输入"


def create_client(cfg: SerialConfig) -> ModbusSerialClient:
    return ModbusSerialClient(
        port=cfg.port,
        baudrate=cfg.baudrate,
        parity=cfg.parity,
        stopbits=cfg.stopbits,
        bytesize=cfg.bytesize,
        timeout=cfg.timeout_sec,
    )


def extract_registers(
    start_address: int,
    values: Iterable[int],
    register_map: Mapping[int, str],
) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for offset, value in enumerate(values or []):
        addr = start_address + offset
        name = register_map.get(addr)
        if name is not None:
            result[name] = value
    return result


def read_slave_once(
    client: ModbusSerialClient,
    bus_name: str,
    slave_id: int,
) -> SlaveState:
    registers: Dict[str, int | float] = {}

    if not client.connected and not client.connect():
        return SlaveState(
            slave_id=slave_id,
            bus_name=bus_name,
            registers=registers,
            ok=False,
            message="串口未连接",
            last_read=None,
        )

    for start_address, count in REGISTER_BLOCKS:
        try:
            response = client.read_holding_registers(
                address=start_address,
                count=count,
                device_id=slave_id,
            )
        except Exception as exc:  # noqa: BLE001
            return SlaveState(
                slave_id=slave_id,
                bus_name=bus_name,
                registers=registers,
                ok=False,
                message=f"读取异常: {exc}",
                last_read=None,
            )

        if response is None:
            return SlaveState(
                slave_id=slave_id,
                bus_name=bus_name,
                registers=registers,
                ok=False,
                message="响应为空",
                last_read=None,
            )

        if isinstance(response, ModbusException) or response.isError():
            return SlaveState(
                slave_id=slave_id,
                bus_name=bus_name,
                registers=registers,
                ok=False,
                message=f"Modbus错误: {response}",
                last_read=None,
            )

        registers.update(extract_registers(start_address, response.registers, HP_REGISTER_MAP))

    return SlaveState(
        slave_id=slave_id,
        bus_name=bus_name,
        registers=registers,
        ok=True,
        message="OK",
        last_read=time.time(),
    )


def format_value(value: int | float | None) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def clear_and_addnstr(
    stdscr: curses.window,
    y: int,
    x: int,
    width: int,
    text: str,
    attr: int = 0,
) -> None:
    if width <= 0:
        return

    def _char_width(ch: str) -> int:
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            return 2
        return 1

    def _fit_to_cells(s: str, cells: int) -> str:
        out: List[str] = []
        used = 0
        for ch in s:
            w = _char_width(ch)
            if used + w > cells:
                break
            out.append(ch)
            used += w
        if used < cells:
            out.append(" " * (cells - used))
        return "".join(out)

    try:
        stdscr.addstr(y, x, " " * width, attr)
        stdscr.addstr(y, x, _fit_to_cells(text, width), attr)
    except curses.error:
        # curses在屏幕边界可能抛异常，忽略以保持刷新循环稳定。
        pass


def build_register_pairs(state: SlaveState) -> List[Tuple[int, str, str]]:
    pairs: List[Tuple[int, str, str]] = []
    for addr, reg_name in NORMAL_REGISTER_ORDER:
        pairs.append((addr, reg_name, format_value(state.registers.get(reg_name))))

    if not state.ok:
        pairs.append((0xFFFF, "error", state.message))

    return pairs


def slave_block_height(state: SlaveState) -> int:
    pairs = build_register_pairs(state)
    items_per_row = max(1, REGISTER_ITEMS_PER_ROW)
    first_ro_idx = next((idx for idx, (addr, _name, _value) in enumerate(pairs) if addr >= 0x006D), len(pairs))
    rw_count = first_ro_idx
    ro_count = max(0, len(pairs) - first_ro_idx)
    rw_rows = (rw_count + items_per_row - 1) // items_per_row
    ro_rows = (ro_count + items_per_row - 1) // items_per_row
    rw_bit_rows = len(RW_BIT_REGISTER_ADDRS)
    ro_bit_rows = len(RO_BIT_REGISTER_ADDRS)
    return 2 + rw_bit_rows + rw_rows + ro_bit_rows + ro_rows


def render_slave_block(
    stdscr: curses.window,
    y: int,
    width: int,
    state: SlaveState,
) -> None:
    status = "在线" if state.ok else "离线"
    timestamp = "--"
    if state.last_read is not None:
        timestamp = time.strftime("%H:%M:%S", time.localtime(state.last_read))

    stdscr.addnstr(y, 0, f"ID:{state.slave_id}  总线:{state.bus_name}", width - 1)
    stdscr.addnstr(y + 1, 0, f"在线状态:{status}  更新时间:{timestamp}", width - 1)

    def build_control_bit_pairs(control_value: int | float | None, bit_mapping: Mapping[int, str]) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        for bit in range(7, -1, -1):
            bit_name = bit_mapping.get(bit, f"bit{bit}")
            if control_value is None:
                bit_val = "NA"
            else:
                bit_val = str((int(control_value) >> bit) & 1)
            pairs.append((bit_name, bit_val))
        return pairs

    rw_bit_rows: List[List[Tuple[str, str]]] = []
    for addr in RW_BIT_REGISTER_ADDRS:
        reg_name = HP_REGISTER_MAP.get(addr, f"0x{addr:04X}")
        rw_bit_rows.append(build_control_bit_pairs(state.registers.get(reg_name), BIT_TRANSLATE[addr]))

    ro_bit_rows: List[List[Tuple[str, str]]] = []
    for addr in RO_BIT_REGISTER_ADDRS:
        reg_name = HP_REGISTER_MAP.get(addr, f"0x{addr:04X}")
        ro_bit_rows.append(build_control_bit_pairs(state.registers.get(reg_name), BIT_TRANSLATE[addr]))

    usable_width = max(1, width - 1)
    bit_group_width = usable_width // 8
    if bit_group_width % 2 == 1:
        bit_group_width -= 1
    if bit_group_width < 2:
        bit_group_width = 2
    bit_name_width = bit_group_width // 2
    bit_value_width = bit_group_width // 2

    name_attr = curses.color_pair(NAME_COLOR_PAIR) | curses.A_BOLD
    value_attr = curses.color_pair(VALUE_COLOR_PAIR) | curses.A_BOLD
    ro_value_attr = curses.color_pair(RO_VALUE_COLOR_PAIR) | curses.A_BOLD

    def render_bit_pair_rows(start_y: int, rows: Iterable[List[Tuple[str, str]]], value_cell_attr: int) -> None:
        for row_offset, bit_pairs in enumerate(rows):
            for idx, (name, value) in enumerate(bit_pairs):
                col_x = idx * bit_group_width
                clear_and_addnstr(
                    stdscr,
                    start_y + row_offset,
                    col_x,
                    bit_name_width,
                    name,
                    name_attr,
                )
                clear_and_addnstr(
                    stdscr,
                    start_y + row_offset,
                    col_x + bit_name_width,
                    bit_value_width,
                    value,
                    value_cell_attr,
                )

    top_bit_start_y = y + 2
    render_bit_pair_rows(top_bit_start_y, rw_bit_rows, value_attr)

    items_per_row = max(1, REGISTER_ITEMS_PER_ROW)
    group_width = usable_width // items_per_row
    if group_width % 2 == 1:
        group_width -= 1
    if group_width < 2:
        group_width = 2

    name_width = group_width // 2
    value_width = group_width // 2

    pairs = build_register_pairs(state)
    first_ro_idx = next((idx for idx, (addr, _name, _value) in enumerate(pairs) if addr >= 0x006D), len(pairs))
    rw_pairs = pairs[:first_ro_idx]
    ro_pairs = pairs[first_ro_idx:]
    rw_rows = (len(rw_pairs) + items_per_row - 1) // items_per_row

    row_y = top_bit_start_y + len(rw_bit_rows)
    for idx, (addr, name, value) in enumerate(rw_pairs):
        if addr == -1:
            continue
        row_idx = idx // items_per_row
        col_idx = idx % items_per_row
        col_x = col_idx * group_width
        cur_y = row_y + row_idx

        name_text = name[:name_width].ljust(name_width)
        value_text = value[:value_width].ljust(value_width)

        clear_and_addnstr(stdscr, cur_y, col_x, name_width, name_text, name_attr)
        clear_and_addnstr(stdscr, cur_y, col_x + name_width, value_width, value_text, value_attr)

    ro_bit_start_y = row_y + rw_rows
    render_bit_pair_rows(ro_bit_start_y, ro_bit_rows, ro_value_attr)

    ro_grid_start_y = ro_bit_start_y + len(ro_bit_rows)
    for idx, (addr, name, value) in enumerate(ro_pairs):
        if addr == -1:
            continue
        row_idx = idx // items_per_row
        col_idx = idx % items_per_row
        col_x = col_idx * group_width
        cur_y = ro_grid_start_y + row_idx

        name_text = name[:name_width].ljust(name_width)
        value_text = value[:value_width].ljust(value_width)

        clear_and_addnstr(stdscr, cur_y, col_x, name_width, name_text, name_attr)
        clear_and_addnstr(stdscr, cur_y, col_x + name_width, value_width, value_text, ro_value_attr)


def render_control_row(
    stdscr: curses.window,
    y: int,
    width: int,
    control: ControlState,
) -> Tuple[int, int]:
    usable_width = max(1, width - 1)
    group_width = usable_width // 2
    if group_width % 2 == 1:
        group_width -= 1
    if group_width < 2:
        group_width = 2

    name_width = group_width // 2
    value_width = group_width // 2

    name_attr = curses.color_pair(NAME_COLOR_PAIR) | curses.A_BOLD
    value_attr = curses.color_pair(VALUE_COLOR_PAIR) | curses.A_BOLD

    labels = ("地址0x", "值")
    values = (control.address_setting, control.value_setting)

    for idx, (label, value) in enumerate(zip(labels, values)):
        x = idx * group_width
        is_active = idx == control.active_index

        name_text = label[:name_width].ljust(name_width)
        value_text = value[:value_width].ljust(value_width)

        clear_and_addnstr(stdscr, y, x, name_width, name_text, name_attr)
        attr = value_attr | (curses.A_REVERSE if is_active else 0)
        clear_and_addnstr(stdscr, y, x + name_width, value_width, value_text, attr)

    active_value = values[control.active_index][:value_width]
    cursor_x = control.active_index * group_width + name_width + min(len(active_value), max(0, value_width - 1))
    return y, cursor_x


def render_tab_bar(
    stdscr: curses.window,
    y: int,
    width: int,
    control: ControlState,
) -> None:
    usable_width = max(1, width - 1)
    tab_width = max(1, usable_width // len(SLAVE_IDS))
    for idx, sid in enumerate(SLAVE_IDS):
        x = idx * tab_width
        label = f"ID {sid}".ljust(tab_width)
        attr = curses.A_REVERSE if idx == control.selected_slave_index else 0
        clear_and_addnstr(stdscr, y, x, tab_width, label, attr)


def render_screen(
    stdscr: curses.window,
    states: Mapping[int, SlaveState],
    poll_interval: float,
    control: ControlState,
) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()

    header = (
        "Heat Pump Modbus RTU Monitor (AMA5:1,3,4 / AMA3:5,6,7)"
        f"  Poll:{poll_interval:.2f}s"
    )
    stdscr.addnstr(0, 0, header, width - 1)

    row = 1
    render_tab_bar(stdscr, row, width, control)
    row += 1

    selected_slave_id = SLAVE_IDS[control.selected_slave_index]
    block_height = slave_block_height(states[selected_slave_id])
    if row + block_height - 1 < height - 2:
        render_slave_block(stdscr, row, width, states[selected_slave_id])

    control_y = max(0, height - 2)
    hint_y = max(0, height - 1)
    cursor_y, cursor_x = render_control_row(stdscr, control_y, width, control)
    stdscr.move(hint_y, 0)
    stdscr.clrtoeol()
    stdscr.addnstr(
        hint_y,
        0,
        f"Ctrl+C:退出  TAB:切换设备  Enter:写入  {control.status_message}",
        width - 1,
    )
    stdscr.move(cursor_y, cursor_x)
    stdscr.refresh()


def poll_bus_once(
    bus_name: str,
    client: ModbusSerialClient,
    slave_ids: Iterable[int],
    out_queue: Queue[Tuple[int, SlaveState]],
    client_lock: Lock,
) -> None:
    for sid in slave_ids:
        with client_lock:
            out_queue.put((sid, read_slave_once(client, bus_name, sid)))


def resolve_bus_by_slave_id(slave_id: int) -> str | None:
    for bus_name, ids in SLAVE_LAYOUT.items():
        if slave_id in ids:
            return bus_name
    return None


def write_register_value(
    clients: Mapping[str, ModbusSerialClient],
    client_locks: Mapping[str, Lock],
    slave_id: int,
    address: int,
    value: int,
) -> Tuple[bool, str]:
    bus_name = resolve_bus_by_slave_id(slave_id)
    if bus_name is None:
        return False, f"无效SlaveID:{slave_id}"

    client = clients[bus_name]
    with client_locks[bus_name]:
        if not client.connected and not client.connect():
            return False, f"{bus_name} 未连接"

        try:
            resp = client.write_register(address=address, value=value, device_id=slave_id)
            if resp is None or isinstance(resp, ModbusException) or resp.isError():
                return False, f"写入0x{address:04X}失败: {resp}"
        except Exception as exc:  # noqa: BLE001
            return False, f"写入异常: {exc}"

    return True, f"已写入 Slave{slave_id} 0x{address:04X}={value}"


def run_ui(stdscr: curses.window, ama5_cfg: SerialConfig, ama3_cfg: SerialConfig, poll_interval: float) -> int:
    curses.curs_set(1)
    stdscr.nodelay(True)
    stdscr.timeout(100)
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(NAME_COLOR_PAIR, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(VALUE_COLOR_PAIR, curses.COLOR_WHITE, curses.COLOR_GREEN)
        curses.init_pair(RO_VALUE_COLOR_PAIR, curses.COLOR_WHITE, curses.COLOR_RED)

    stop = False

    def _handle_signal(_signum, _frame) -> None:  # type: ignore[override]
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    clients = {
        "AMA5": create_client(ama5_cfg),
        "AMA3": create_client(ama3_cfg),
    }
    client_locks = {bus_name: Lock() for bus_name in clients}

    register_cache: Dict[int, Dict[str, int | float | None]] = {
        sid: {reg_name: None for _addr, reg_name in REGISTER_ORDER}
        for bus, slave_ids in SLAVE_LAYOUT.items()
        for sid in slave_ids
    }

    states: Dict[int, SlaveState] = {
        sid: SlaveState(
            slave_id=sid,
            bus_name=bus,
            registers=dict(register_cache[sid]),
            ok=False,
            message="等待首次读取",
            last_read=None,
        )
        for bus, slave_ids in SLAVE_LAYOUT.items()
        for sid in slave_ids
    }
    control = ControlState()

    def _commit_write() -> None:
        control.active_index = 0
        addr_text = control.address_setting.strip()
        value_text = control.value_setting.strip()
        if not addr_text:
            control.status_message = "请输入地址(4位十六进制)"
            return
        if len(addr_text) > 4:
            control.status_message = "地址最多4位十六进制"
            return

        try:
            address = int(addr_text, 16)
        except ValueError:
            control.status_message = "地址必须是十六进制数字(0-9,A-F)"
            return

        try:
            value = int(value_text)
        except ValueError:
            control.status_message = "值必须是十进制数字"
            return

        if not (0 <= address <= 0xFFFF and 0 <= value <= 65535):
            control.status_message = "地址/值范围必须是0~65535"
            return

        slave_id = SLAVE_IDS[control.selected_slave_index]
        ok, msg = write_register_value(
            clients,
            client_locks,
            slave_id=slave_id,
            address=address,
            value=value,
        )
        if ok and slave_id in register_cache:
            register_name = HP_REGISTER_MAP.get(address)
            if register_name is not None:
                register_cache[slave_id][register_name] = value
            states[slave_id] = SlaveState(
                slave_id=states[slave_id].slave_id,
                bus_name=states[slave_id].bus_name,
                registers=dict(register_cache[slave_id]),
                ok=states[slave_id].ok,
                message=states[slave_id].message,
                last_read=states[slave_id].last_read,
            )
        control.status_message = msg

    def _handle_key(key: int) -> bool:
        if key == -1:
            return False
        if key == 3:  # Ctrl+C
            return True
        if key in (10, 13, curses.KEY_ENTER):  # Enter
            _commit_write()
            return False

        if key == 9:  # TAB
            control.selected_slave_index = (control.selected_slave_index + 1) % len(SLAVE_IDS)
            return False
        if key == curses.KEY_BTAB:
            control.selected_slave_index = (control.selected_slave_index - 1) % len(SLAVE_IDS)
            return False
        if key == curses.KEY_LEFT:
            control.active_index = (control.active_index - 1) % 2
            return False
        if key == curses.KEY_RIGHT:
            control.active_index = (control.active_index + 1) % 2
            return False

        values = [control.address_setting, control.value_setting]
        cur = values[control.active_index]
        if key in (curses.KEY_BACKSPACE, 127, 8):
            cur = cur[:-1]
        elif 48 <= key <= 57:
            if control.active_index == 0:
                if len(cur) < 4:
                    cur += chr(key)
            else:
                if len(cur) < 5:
                    cur += chr(key)
        elif control.active_index == 0 and 65 <= key <= 70:
            if len(cur) < 4:
                cur += chr(key)
        elif control.active_index == 0 and 97 <= key <= 102:
            if len(cur) < 4:
                cur += chr(key).upper()
        else:
            return False

        if control.active_index == 0:
            control.address_setting = cur.upper()
        else:
            control.value_setting = cur
        return False

    try:
        render_screen(stdscr, states, poll_interval, control)

        while not stop:
            cycle_start = time.time()
            updates: Queue[Tuple[int, SlaveState]] = Queue()
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(
                        poll_bus_once,
                        bus_name,
                        clients[bus_name],
                        slave_ids,
                        updates,
                        client_locks[bus_name],
                    )
                    for bus_name, slave_ids in SLAVE_LAYOUT.items()
                ]

                expected = sum(len(slave_ids) for slave_ids in SLAVE_LAYOUT.values())
                processed = 0
                while processed < expected and not stop:
                    try:
                        sid, result = updates.get(timeout=0.05)
                    except Empty:
                        if all(f.done() for f in futures):
                            break
                    else:
                        if result.registers:
                            register_cache[sid].update(result.registers)

                        prev_last_read = states[sid].last_read
                        states[sid] = SlaveState(
                            slave_id=sid,
                            bus_name=result.bus_name,
                            registers=dict(register_cache[sid]),
                            ok=result.ok,
                            message=result.message,
                            last_read=result.last_read if result.last_read is not None else prev_last_read,
                        )
                        processed += 1
                        render_screen(stdscr, states, poll_interval, control)

                    key = stdscr.getch()
                    if _handle_key(key):
                        stop = True
                        break
                    if key != -1:
                        render_screen(stdscr, states, poll_interval, control)

                for future in futures:
                    future.result()

            end_ts = cycle_start + poll_interval
            while time.time() < end_ts:
                key = stdscr.getch()
                if _handle_key(key):
                    stop = True
                    break
                if key != -1:
                    render_screen(stdscr, states, poll_interval, control)
                time.sleep(0.02)
    finally:
        for client in clients.values():
            try:
                client.close()
            except Exception:
                pass

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="6台热泵 Modbus RTU 终端状态监视器")
    parser.add_argument("--ama5-port", default="/dev/ttyAMA5", help="AMA5 串口设备")
    parser.add_argument("--ama3-port", default="/dev/ttyAMA3", help="AMA3 串口设备")
    parser.add_argument("--baudrate", type=int, default=9600, help="串口波特率")
    parser.add_argument("--parity", default="N", help="串口校验位 N/E/O")
    parser.add_argument("--stopbits", type=int, default=1, help="停止位")
    parser.add_argument("--bytesize", type=int, default=8, help="数据位")
    parser.add_argument("--timeout", type=float, default=2.0, help="串口超时时间(秒)")
    parser.add_argument("--poll", type=float, default=0.5, help="轮询间隔(秒)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    ama5_cfg = SerialConfig(
        port=args.ama5_port,
        baudrate=args.baudrate,
        parity=args.parity,
        stopbits=args.stopbits,
        bytesize=args.bytesize,
        timeout_sec=args.timeout,
    )
    ama3_cfg = SerialConfig(
        port=args.ama3_port,
        baudrate=args.baudrate,
        parity=args.parity,
        stopbits=args.stopbits,
        bytesize=args.bytesize,
        timeout_sec=args.timeout,
    )

    return curses.wrapper(run_ui, ama5_cfg, ama3_cfg, args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
