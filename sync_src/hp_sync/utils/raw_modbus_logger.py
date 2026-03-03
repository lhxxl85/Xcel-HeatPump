from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import threading
from typing import TextIO


class RawModbusFrameLogger:
    """Write raw Modbus RTU frames to daily files and keep recent days only."""

    def __init__(
        self,
        log_dir: str = "logs",
        filename_prefix: str = "raw_modbus",
        retention_days: int = 7,
    ) -> None:
        self.log_dir = Path(log_dir)
        self.filename_prefix = filename_prefix
        self.retention_days = max(1, int(retention_days))
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._current_date: date | None = None
        self._stream: TextIO | None = None

        today = datetime.now().date()
        self._open_for_date(today)
        self._cleanup_old_files(today)

    def close(self) -> None:
        with self._lock:
            if self._stream is not None:
                self._stream.close()
                self._stream = None

    def log_frame(self, sending: bool, frame: bytes) -> None:
        if not frame:
            return
        if not sending and not self._is_complete_rtu_frame(frame):
            # pymodbus sync path may pass a growing RX buffer multiple times.
            # Only log once we have a complete RTU frame with valid CRC.
            return

        now = datetime.now()
        direction = "TX" if sending else "RX"
        payload = " ".join(f"{byte:02X}" for byte in frame)
        line = f"{now:%Y-%m-%d %H:%M:%S.%f}\t{direction}\t{payload}\n"

        with self._lock:
            if now.date() != self._current_date:
                self._open_for_date(now.date())
                self._cleanup_old_files(now.date())

            if self._stream is None:
                self._open_for_date(now.date())

            self._stream.write(line)
            self._stream.flush()

    def _file_path_for_date(self, day: date) -> Path:
        return self.log_dir / f"{self.filename_prefix}_{day:%Y%m%d}.log"

    def _open_for_date(self, day: date) -> None:
        if self._stream is not None:
            self._stream.close()
        self._stream = self._file_path_for_date(day).open("a", encoding="utf-8")
        self._current_date = day

    def _cleanup_old_files(self, today: date) -> None:
        # Keep today's file plus (retention_days - 1) previous days.
        cutoff = today - timedelta(days=self.retention_days - 1)
        prefix = f"{self.filename_prefix}_"

        for path in self.log_dir.glob(f"{prefix}*.log"):
            stem = path.stem
            date_part = stem.removeprefix(prefix)
            if len(date_part) != 8 or not date_part.isdigit():
                continue
            try:
                file_date = datetime.strptime(date_part, "%Y%m%d").date()
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    # Best effort cleanup; logging must not fail because of old files.
                    pass

    @staticmethod
    def _is_complete_rtu_frame(frame: bytes) -> bool:
        expected_len = RawModbusFrameLogger._expected_rtu_len(frame)
        if expected_len is None or len(frame) != expected_len:
            return False
        payload = frame[:-2]
        crc_in_frame = int.from_bytes(frame[-2:], byteorder="big", signed=False)
        return RawModbusFrameLogger._compute_crc(payload) == crc_in_frame

    @staticmethod
    def _expected_rtu_len(frame: bytes) -> int | None:
        if len(frame) < 2:
            return None
        func = frame[1]

        # Exception response: dev + func + ex_code + crc(2)
        if func & 0x80:
            return 5

        # Read response format: dev + func + byte_count + data + crc(2)
        if func in (0x01, 0x02, 0x03, 0x04):
            if len(frame) < 3:
                return None
            byte_count = frame[2]
            return 5 + byte_count

        # Common fixed-length request/response frames.
        if func in (0x05, 0x06, 0x0F, 0x10):
            return 8

        # Unknown function code: do not log partial/ambiguous frame.
        return None

    @staticmethod
    def _compute_crc(data: bytes) -> int:
        crc = 0xFFFF
        for data_byte in data:
            crc ^= data_byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return ((crc << 8) & 0xFF00) | ((crc >> 8) & 0x00FF)
