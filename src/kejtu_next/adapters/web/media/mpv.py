"""Private mpv IPC shared by the dedicated radio and video players."""
from contextlib import contextmanager
import json
import os
import socket
import stat
import struct
import subprocess
import sys
from pathlib import Path
import time
from .errors import Cancelled
from .errors import WorkspaceError


class PlayerDisconnected(WorkspaceError):
    pass


class MpvController:
    label = "Player"

    @staticmethod
    def _supported():
        if sys.platform != 'linux':
            raise WorkspaceError('Verified mpv playback is currently supported on Linux desktop sessions only.')

    @property
    def identity_path(self):
        return self.directory / 'player.json'

    def _identity(self,pid):
        self._supported()
        root=Path('/proc')/str(pid)
        command=(root/'cmdline').read_bytes().split(b'\0')
        if (root/'exe').resolve()!=Path('/usr/bin/mpv').resolve() or ('--input-ipc-server='+str(self.socket_path)).encode() not in command:
            raise WorkspaceError('The socket does not belong to this dedicated mpv instance.')
        # /proc stat's command may contain spaces and parentheses.
        start=(root/'stat').read_text().rsplit(')',1)[1].split()[19]
        return dict(pid=pid,start=start,uid=os.getuid(),socket=str(self.socket_path))

    def _record_started(self):
        # Type=simple can acknowledge the start before exec/MainPID is visible.
        # Repeat observation only, never the launch or a playback effect.
        while True:
            self._check()
            result=subprocess.run(['/usr/bin/systemctl','--user','show',self.unit,'--property=MainPID','--value'],
                                  capture_output=True,text=True,timeout=min(3,max(.1,self.deadline-time.monotonic())),check=False)
            try:
                identity=self._identity(int(result.stdout.strip()))
                break
            except (ValueError,OSError):time.sleep(.05)
        fd=os.open(self.identity_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as output:json.dump(identity,output)

    def _desktop_environment(self):
        # A player window must follow the requesting desktop session. These are
        # operator environment values, never model-supplied switches or commands.
        return ['--setenv='+name+'='+os.environ[name] for name in ('DISPLAY','XAUTHORITY','WAYLAND_DISPLAY')
                if os.environ.get(name) and '\x00' not in os.environ[name]]

    def _clear_stale_socket(self):
        if not self.socket_path.exists():return
        # _alive has already rejected a reused or unverifiable live identity.
        if self._alive():raise WorkspaceError('This dedicated player is already running.')
        info=self.socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():
            raise WorkspaceError('The stale player socket is not owned by this account.')
        self.socket_path.unlink()

    def _verify_peer(self,connection):
        if not hasattr(socket,'SO_PEERCRED'):
            raise WorkspaceError('Verified mpv control is currently supported on Linux desktop sessions.')
        pid,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        try:
            info=self.identity_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                raise WorkspaceError('The player identity is not private and owned by you.')
            if info.st_size>2048:raise WorkspaceError('The player identity is invalid.')
            saved=json.loads(self.identity_path.read_text())
            if uid!=os.getuid() or saved!=self._identity(pid):
                raise WorkspaceError('The player identity changed; no command was sent.')
        except (OSError,ValueError):
            raise WorkspaceError('The player identity is unavailable or changed; no command was sent.') from None
    def _check(self):
        if self.cancelled.is_set():
            raise Cancelled()
        if time.monotonic() >= self.deadline:
            raise WorkspaceError(f"{self.label} did not finish in time. Check {self.label.lower()} status before trying again.")

    @contextmanager
    def _lock(self):
        # Optional Linux players must not prevent application imports elsewhere.
        # Refuse before creating a runtime directory or touching a player.
        self._supported()
        import fcntl
        if not self.directory.parent.is_dir() or self.directory.parent.resolve() != self.directory.parent:
            raise WorkspaceError(f"A local desktop runtime directory is required for {self.label.lower()}.")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise WorkspaceError(f"The {self.label.lower()} runtime directory must be private and owned by you.")
        fd = os.open(self.directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            while True:
                self._check()
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)

    def _rpc(self, command: list, *, optional: bool = False):
        self._check()
        try:
            info = self.socket_path.lstat()
        except FileNotFoundError:
            raise WorkspaceError(f"{self.label} is stopped. Use {self.label.lower()} play followed by a supported source.") from None
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise WorkspaceError(f"The {self.label.lower()} control socket is not a private player socket.")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(min(2.0, max(0.01, self.deadline - time.monotonic())))
            connection.connect(str(self.socket_path))
            self._verify_peer(connection)
            connection.sendall((json.dumps({"command": command, "request_id": 1}) + "\n").encode())
            buffer = b""
            count = 0
            while True:
                self._check()
                chunk = connection.recv(4096)
                if not chunk:
                    raise PlayerDisconnected(f"The {self.label.lower()} player closed its connection without confirming the command.")
                buffer += chunk
                count += len(chunk)
                if count > 128 * 1024:
                    raise WorkspaceError(f"{self.label} returned too much data.")
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    reply = json.loads(line)
                    if reply.get("request_id") != 1:
                        continue
                    if reply.get("error") != "success":
                        if optional and reply.get("error") in ("property unavailable", "property not found"):
                            return None
                        raise WorkspaceError(f"{self.label} player: " + str(reply.get("error")))
                    return reply.get("data")

    def _alive(self):
        if not self.socket_path.exists():
            return False
        for attempt in range(2):
            try:
                self._rpc(["get_property", "idle-active"])
                return True
            except (ConnectionRefusedError, FileNotFoundError):
                return False
            except (ConnectionResetError, BrokenPipeError, PlayerDisconnected):
                # mpv can close IPC while exiting. Recheck; a reset alone does
                # not prove that the player stopped.
                if attempt:
                    raise
                time.sleep(.05)
            except WorkspaceError:
                if not self.socket_path.exists():
                    return False
                raise

    def _quit(self):
        if self._alive():
            try:
                self._rpc(['quit'])
            except (ConnectionResetError, BrokenPipeError, PlayerDisconnected):
                pass  # Exit may win the race with the acknowledgement.
            while self._alive():
                self._check()
                time.sleep(.05)
