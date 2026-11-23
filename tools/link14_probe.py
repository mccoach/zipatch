# -*- coding: utf-8 -*-

"""第14项专项：定义读取与正常跟随访问分别测试。

默认只读；--write-probe仅修改本程序新建的独占测试文件。
不修改既有正文、ACL或原链接，不提权，不删除重建原入口。
报告写入stdout；脚本不作为Zipatch正式读写旁路。
"""

import argparse
import ctypes
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path


DEFAULT_LINK = (
    r"C:\Users\micha\AppData\Local\Temp"
    r"\Zipatch_UI_i8cnkukq\project\recover-folder"
)
OPEN_EXISTING = 3
SHARE_ALL = 7
BACKUP_SEMANTICS = 0x02000000
OPEN_REPARSE_POINT = 0x00200000
GET_REPARSE_POINT = 0x000900A8
INVALID_HANDLE = ctypes.c_void_p(-1).value


def emit(name, **facts):
    print(json.dumps(
        {"test": name, **facts}, ensure_ascii=False, default=str,
    ), flush=True)


def error_facts(error):
    return {
        "error_type": type(error).__name__, "error": str(error),
        "errno": getattr(error, "errno", None),
        "winerror": getattr(error, "winerror", None),
    }


def attempt(name, operation):
    try:
        value = operation()
    except (OSError, ValueError) as error:
        emit(name, ok=False, **error_facts(error))
        return None
    emit(name, ok=True, result=value)
    return value


def attributes(path, follow=False):
    value = os.stat(path, follow_symlinks=follow)
    return {
        "mode": oct(value.st_mode),
        "attributes": hex(getattr(value, "st_file_attributes", 0)),
        "tag": hex(getattr(value, "st_reparse_tag", 0)),
        "identity": [value.st_dev, value.st_ino],
        "links": value.st_nlink,
    }


def parse_reparse(raw):
    if len(raw) < 8:
        raise ValueError("重解析数据不足8字节")
    tag, length, reserved = struct.unpack_from("<IHH", raw)
    end = 8 + length
    if end > len(raw):
        raise ValueError("重解析数据长度越界")
    if tag == 0xA000000C:
        if length < 12:
            raise ValueError("软链接重解析头不完整")
        sub_offset, sub_length, print_offset, print_length, flags = struct.unpack_from(
            "<HHHHI", raw, 8,
        )
        base = 20
    elif tag == 0xA0000003:
        if length < 8:
            raise ValueError("联接重解析头不完整")
        sub_offset, sub_length, print_offset, print_length = struct.unpack_from(
            "<HHHH", raw, 8,
        )
        base, flags = 16, None
    else:
        return {"tag": hex(tag), "length": length, "supported_definition": False}

    def text(offset, size):
        if offset % 2 or size % 2 or base + offset + size > end:
            raise ValueError("重解析字符串范围非法")
        return raw[base + offset:base + offset + size].decode("utf-16-le")

    return {
        "tag": hex(tag), "flags": flags,
        "substitute_name": text(sub_offset, sub_length),
        "print_name": text(print_offset, print_length),
        "raw_hex": raw[:end].hex(),
    }


class WindowsProbe:
    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.open = kernel.CreateFileW
        self.open.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        self.open.restype = wintypes.HANDLE
        self.close = kernel.CloseHandle
        self.close.argtypes = (wintypes.HANDLE,)
        self.close.restype = wintypes.BOOL
        self.control = kernel.DeviceIoControl
        self.control.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        )
        self.control.restype = wintypes.BOOL
        self.final = kernel.GetFinalPathNameByHandleW
        self.final.argtypes = (
            wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
        )
        self.final.restype = wintypes.DWORD

    def final_name(self, handle, flags):
        buffer = ctypes.create_unicode_buffer(32768)
        count = self.final(handle, buffer, len(buffer), flags)
        if not count:
            raise ctypes.WinError(ctypes.get_last_error())
        if count >= len(buffer):
            raise ValueError("句柄路径超过诊断缓冲区")
        return buffer.value

    def query(self, path, access, *, follow):
        handle = self.open(
            str(path), access, SHARE_ALL, None, OPEN_EXISTING,
            BACKUP_SEMANTICS | (0 if follow else OPEN_REPARSE_POINT), None,
        )
        if handle == INVALID_HANDLE:
            emit(
                "native-open", path=str(path), access=hex(access), follow=follow,
                ok=False, stage="CreateFileW",
                **error_facts(ctypes.WinError(ctypes.get_last_error())),
            )
            return None
        emit("native-open", path=str(path), access=hex(access), follow=follow, ok=True)
        result = None
        try:
            if follow:
                for flags in (0, 8):
                    value = attempt(
                        f"final-path-flags-{flags}",
                        lambda flags=flags: self.final_name(handle, flags),
                    )
                    if flags == 0:
                        result = value
            else:
                buffer = ctypes.create_string_buffer(16384)
                returned = wintypes.DWORD()
                if not self.control(
                    handle, GET_REPARSE_POINT, None, 0,
                    buffer, len(buffer), ctypes.byref(returned), None,
                ):
                    emit(
                        "native-reparse", path=str(path), access=hex(access),
                        ok=False, stage="DeviceIoControl",
                        **error_facts(ctypes.WinError(ctypes.get_last_error())),
                    )
                else:
                    result = attempt(
                        "native-reparse",
                        lambda: parse_reparse(buffer.raw[:returned.value]),
                    )
        finally:
            if not self.close(handle):
                emit(
                    "native-close", ok=False,
                    **error_facts(ctypes.WinError(ctypes.get_last_error())),
                )
        return result


def ordinary_path(value):
    if value.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + value[8:])
    if value.startswith("\\\\?\\"):
        return Path(value[4:])
    return Path(value)


def within(root, path):
    left = os.path.normcase(os.path.abspath(root))
    right = os.path.normcase(os.path.abspath(path))
    try:
        return os.path.commonpath((left, right)) == left
    except ValueError:
        return False


def identity_from_stat(value):
    return value.st_dev, value.st_ino


def write_owned_probe(link, resolved, allowed_root):
    """只使用随机专用子目录和新文件；不接触目标内的既有正文。"""
    actual = ordinary_path(resolved)
    if not actual.is_absolute() or not within(allowed_root, actual):
        raise ValueError(
            f"跟随目标不在允许的隔离测试区内，未进行写测试：{actual}"
        )
    current = os.stat(link)
    direct = os.stat(actual)
    if (
        not stat.S_ISDIR(current.st_mode)
        or identity_from_stat(current) != identity_from_stat(direct)
    ):
        raise ValueError("原链接跟随结果不是已定位的同一目录")
    for parent in (*reversed(actual.parents), actual):
        observed = os.lstat(parent)
        if (
            stat.S_ISLNK(observed.st_mode)
            or getattr(observed, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("专用写测试的实际父链还有重解析入口，未进行写测试")

    directory = Path(tempfile.mkdtemp(prefix=".zipatch-link14-", dir=actual))
    owned_directory = identity_from_stat(os.lstat(directory))
    file = directory / "owned-probe.bin"
    nominal = Path(link) / directory.name / file.name
    descriptor = None
    owned_file = None
    try:
        descriptor = os.open(
            file, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_BINARY, 0o600,
        )
        initial = os.fstat(descriptor)
        owned_file = identity_from_stat(initial)
        os.write(descriptor, b"link14-before")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        # 正常链接路径打开，不先读定义；r+b不会创建或预先截断。
        with open(nominal, "r+b", buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or identity_from_stat(opened) != owned_file
                or opened.st_nlink != 1
            ):
                raise ValueError("链接路径没有打开本程序独占的测试文件")
            if stream.read() != b"link14-before":
                raise ValueError("专用测试文件原正文不符")
            stream.seek(0)
            replacement = b"link14-after-via-original-alias"
            if stream.write(replacement) != len(replacement):
                raise OSError("专用测试文件写入不完整")
            stream.truncate(len(replacement))
            os.fsync(stream.fileno())
            stream.seek(0)
            if stream.read() != replacement:
                raise OSError("同一句柄写入结果不符")
        if identity_from_stat(os.lstat(file)) != owned_file:
            raise ValueError("专用测试成果身份变化")
        with open(file, "rb") as stream:
            if stream.read() != replacement:
                raise OSError("实际路径没有看到经原链接路径写入的结果")
        return {
            "nominal_probe": str(nominal), "actual_probe": str(file),
            "same_owned_file_modified": True,
            "existing_body_modified": False,
        }
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # 只按自有身份清理两个精确位置；不递归删除，不改变只读位。
        try:
            if owned_file is not None:
                current = os.lstat(file)
                if identity_from_stat(current) != owned_file or current.st_nlink != 1:
                    raise ValueError("测试文件不再归本程序独占，保留现场")
                os.unlink(file)
            if identity_from_stat(os.lstat(directory)) != owned_directory:
                raise ValueError("测试目录身份变化，保留现场")
            os.rmdir(directory)
            emit("owned-probe-cleanup", ok=True, directory=str(directory))
        except (OSError, ValueError) as error:
            emit(
                "owned-probe-cleanup", ok=False, directory=str(directory),
                **error_facts(error),
            )


def command(name, arguments):
    try:
        completed = subprocess.run(
            arguments, capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        emit(name, ok=False, error_type=type(error).__name__, error=str(error))
        return
    emit(
        name, ok=completed.returncode == 0, exit_code=completed.returncode,
        stdout=completed.stdout.decode("oem", errors="backslashreplace"),
        stderr=completed.stderr.decode("oem", errors="backslashreplace"),
    )


def main():
    if os.name != "nt":
        raise SystemExit("本专项只支持Windows")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--link", default=DEFAULT_LINK)
    parser.add_argument(
        "--target", default="",
        help="若你知道原目标，可填写；只作独立对照，不把它当作现场定义",
    )
    parser.add_argument("--write-probe", action="store_true")
    arguments = parser.parse_args()
    link = Path(arguments.link)
    if not link.is_absolute():
        raise SystemExit("入口必须是完整绝对路径")
    # 默认现场是隔离UI测试区。自定义入口须同时调整显式测试范围，
    # 本脚本不对任意用户目录开展写实验。
    allowed_root = Path(DEFAULT_LINK).parent.parent
    emit(
        "environment", python=sys.version, windows=sys.getwindowsversion(),
        link=str(link), provided_target=arguments.target,
        write_probe=arguments.write_probe,
        notice="写测试只新建专用文件；目标目录时间会因创建清理而变化",
    )
    before = attempt("lstat-before", lambda: attributes(link))
    if before is None:
        emit("summary", scene_available=False, note="不重建原现场")
        return 2
    spellings = tuple(dict.fromkeys((
        str(link), "\\\\?\\" + str(link), str(link).replace("\\", "/"),
    )))
    for spelling in spellings:
        attempt("python-readlink", lambda spelling=spelling: os.readlink(spelling))
    attempt("python-follow-stat", lambda: attributes(link, True))
    command("fsutil", ["fsutil", "reparsepoint", "query", str(link)])
    command("icacls-entry", ["icacls", str(link), "/L"])
    native = WindowsProbe()
    # 不启用任何特权；尝试不同的明确访问请求。
    for spelling in spellings[:2]:
        for access in (0, 0x80, 0x80000000, 0x20000):
            native.query(spelling, access, follow=False)
    resolved = None
    for access in (0, 0x80):
        result = native.query(link, access, follow=True)
        if isinstance(result, str) and resolved is None:
            resolved = result
    if arguments.target:
        target = Path(arguments.target)
        attempt("provided-target-lstat", lambda: attributes(target))
        attempt("provided-target-samefile", lambda: os.path.samefile(link, target))
    if arguments.write_probe:
        if resolved is None:
            emit(
                "alias-write-probe", ok=False,
                note="正常跟随句柄未取得实际目录，未新建或修改测试文件",
            )
        else:
            attempt(
                "alias-write-probe",
                lambda: write_owned_probe(link, resolved, allowed_root),
            )
    after = attempt("lstat-after", lambda: attributes(link))
    emit(
        "summary", scene_available=True,
        original_entry_identity_unchanged=(
            before["identity"] == after["identity"] if after else None
        ),
        followed_path=resolved,
        original_link_recreated=False, permissions_changed=False,
        note="各试验成功只证明对应能力，不授予Zipatch正式修改资格",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())