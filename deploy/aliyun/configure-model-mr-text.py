#!/usr/bin/env python3
"""Owner-operated, hidden-input setup. No ASR reads, restart, or model calls."""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import warnings

CONFIG_DIR = Path('/etc/instant-ai')
CONFIG_FILE = CONFIG_DIR / 'model-mr-text-secrets.env'
DROPIN_DIR = Path('/etc/systemd/system/instant-ai.service.d')
DROPIN_FILE = DROPIN_DIR / '40-model-mr-text.conf'
MODEL = 'doubao-seed-2-1-turbo-260628'
DROPIN = '[Service]\nEnvironmentFile=/etc/instant-ai/model-mr-text-secrets.env\n'


class SetupError(Exception):
    pass


def check_directory(path: Path) -> None:
    """Check metadata only; do not follow symbolic links or read credentials."""
    for part in reversed((path, *path.parents)):
        value = part.lstat()
        if (not stat.S_ISDIR(value.st_mode) or value.st_uid != 0
                or value.st_mode & 0o022):
            raise SetupError('配置目录所有权、权限或链接异常；未修改任何现有文件。')


def require_absent(path: Path) -> None:
    if os.path.lexists(path):
        raise SetupError('文本配置或服务配置已存在；已停止，未读取、覆盖或重复创建。')


def credential_payload(first: str, second: str) -> bytes:
    # No stripping: pasted newlines/whitespace must be rejected, not normalized.
    if not re.fullmatch(r'[A-Za-z0-9._:/+=-]{16,512}', first):
        raise SetupError('口令格式不符合要求；请重新检查方舟 API Key，内容不会显示。')
    if first != second:
        raise SetupError('两次输入不一致；未写入配置。')
    return (f'INSTANT_AI_DOUBAO_ARK_API_KEY={first}\n'
            f'INSTANT_AI_DOUBAO_TEXT_MODEL={MODEL}\n').encode('ascii')


def remove_owned_new(path: Path, identity: tuple[int, int]) -> None:
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) == identity:
            path.unlink()
    except FileNotFoundError:
        pass


def write_new(path: Path, payload: bytes, mode: int) -> tuple[int, int]:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    value = os.fstat(descriptor)
    identity = (value.st_dev, value.st_ino)
    try:
        os.fchmod(descriptor, mode)
        os.fchown(descriptor, 0, 0)
        # fdopen owns/always closes the descriptor, including failed writes.
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        remove_owned_new(path, identity)
        raise
    return identity


def install_pair(payload: bytes) -> None:
    require_absent(CONFIG_FILE)
    require_absent(DROPIN_FILE)
    identity = write_new(CONFIG_FILE, payload, 0o600)
    try:
        write_new(DROPIN_FILE, DROPIN.encode('ascii'), 0o644)
    except BaseException:
        # Only remove the new file created by this invocation, never old config.
        remove_owned_new(CONFIG_FILE, identity)
        raise


def main() -> int:
    if sys.argv[1:] == ['--help']:
        print('由主人在服务器 root 交互终端运行，不接受密码/密钥参数。')
        print('仅新增独立文本配置并重载服务定义；不读取语音配置、不重启、不发布、不调用模型。')
        return 0
    if sys.argv[1:]:
        print('不接受命令参数；请使用隐藏输入提示。', file=sys.stderr)
        return 2
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        print('需要服务器 root 交互终端；没有执行任何配置。', file=sys.stderr)
        return 2
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        print('必须使用交互终端，拒绝管道输入，避免密钥回显或进入日志。', file=sys.stderr)
        return 2
    try:
        check_directory(CONFIG_DIR)
        check_directory(DROPIN_DIR if DROPIN_DIR.exists() else DROPIN_DIR.parent)
        require_absent(CONFIG_FILE)
        require_absent(DROPIN_FILE)
        print('此步骤只新增豆包关键词模型配置，不影响现有语音识别。')
        print('不会开始识别或提炼。新配置在另行正式发布重启后才会加载。')
        if input('确认配置请输入 SETUP（其他输入取消）：') != 'SETUP':
            print('已取消，未写入配置。')
            return 0
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            first = getpass.getpass('请输入火山方舟 API Key（不显示）：')
            second = getpass.getpass('请再次输入同一 API Key（不显示）：')
        payload = credential_payload(first, second)
        first = second = ''
        DROPIN_DIR.mkdir(mode=0o755, exist_ok=True)
        check_directory(DROPIN_DIR)
        install_pair(payload)
        payload = b''
        result = subprocess.run(['/usr/bin/systemctl', 'daemon-reload'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False, timeout=30)
        if result.returncode:
            print('配置已安全写入，但服务定义重载失败；请先检查，不要重复运行配置入口。', file=sys.stderr)
            return 1
        print('TEXT_CONFIG_SAVED：独立 root:root 0600 配置已写入。')
        print('未重启、未发布、未调用模型；等正式发布后再验证模型可用性。')
        return 0
    except (KeyboardInterrupt, EOFError):
        print('\n输入或写入已中止；未重启或调用模型。', file=sys.stderr)
        return 1
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 1
    except getpass.GetPassWarning:
        print('终端无法隐藏输入，已停止。', file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print('配置已写入，服务定义重载超时；请先检查，不要重复配置。', file=sys.stderr)
        return 1
    except OSError:
        print('配置未正常完成；请检查目录权限或文件冲突，未重启或调用模型。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
