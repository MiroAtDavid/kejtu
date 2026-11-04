//! Backend-only ownership. Windows job membership is established by CreateProcessW,
//! before child code can run; existing processes and shared services are never adopted.
//! This is the backend builder's supported Command shape, not general Command parity:
//! inherited environment plus get_envs overrides, explicit cwd and fixed stdio.
//! Command::env_clear and arbitrary caller-configured stdio are not implemented.
#[cfg(unix)]
mod unix {
    use std::process::{Child, Command};
    use std::{io, thread, time::Duration};
    pub(crate) struct OwnedChild(pub(crate) Child);
    impl OwnedChild {
        pub(crate) fn spawn(command: &mut Command, _: bool) -> io::Result<Self> {
            command.spawn().map(Self)
        }
    }
    impl std::ops::Deref for OwnedChild {
        type Target = Child;
        fn deref(&self) -> &Child {
            &self.0
        }
    }
    impl std::ops::DerefMut for OwnedChild {
        fn deref_mut(&mut self) -> &mut Child {
            &mut self.0
        }
    }
    impl Drop for OwnedChild {
        fn drop(&mut self) {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            unsafe {
                libc::kill(self.0.id() as i32, libc::SIGTERM);
            }
            for _ in 0..50 {
                if self.0.try_wait().ok().flatten().is_some() {
                    return;
                }
                thread::sleep(Duration::from_millis(100));
            }
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}
#[cfg(unix)]
pub(crate) use unix::OwnedChild;

#[cfg(windows)]
mod windows {
    use std::ffi::{c_void, OsStr, OsString};
    use std::fs::File;
    use std::io::{self, Read};
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use std::os::windows::process::ExitStatusExt;
    use std::process::{Command, ExitStatus};
    use std::ptr::{null, null_mut};
    use std::sync::{Arc, Mutex, OnceLock};
    use std::time::{Duration, Instant};
    use windows_sys::Win32::Foundation::*;
    use windows_sys::Win32::Globalization::CompareStringOrdinal;
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::Console::{GetStdHandle, STD_ERROR_HANDLE};
    use windows_sys::Win32::System::JobObjects::*;
    use windows_sys::Win32::System::Pipes::{CreatePipe, PeekNamedPipe};
    use windows_sys::Win32::System::Threading::*;

    const FORCE_EXIT: u32 = 0xE0000001;
    fn ok(value: i32) -> io::Result<()> {
        if value == 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }
    fn owned(raw: HANDLE) -> io::Result<OwnedHandle> {
        if raw.is_null() || raw == INVALID_HANDLE_VALUE {
            Err(io::Error::last_os_error())
        } else {
            Ok(unsafe { OwnedHandle::from_raw_handle(raw as _) })
        }
    }
    fn raw(handle: &OwnedHandle) -> HANDLE {
        handle.as_raw_handle() as _
    }
    fn wide(value: &OsStr) -> io::Result<Vec<u16>> {
        let mut result: Vec<u16> = value.encode_wide().collect();
        if result.contains(&0) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "NUL in backend launch",
            ));
        }
        result.push(0);
        Ok(result)
    }
    fn duplicate(handle: HANDLE, access: u32, inherit: bool) -> io::Result<OwnedHandle> {
        let mut result = null_mut();
        unsafe {
            ok(DuplicateHandle(
                GetCurrentProcess(),
                handle,
                GetCurrentProcess(),
                &mut result,
                access,
                inherit as i32,
                0,
            ))?;
        }
        owned(result)
    }
    fn inheritable(handle: &OwnedHandle) -> io::Result<OwnedHandle> {
        copy_inheritable(raw(handle))
    }
    fn copy_inheritable(handle: HANDLE) -> io::Result<OwnedHandle> {
        let mut result = null_mut();
        unsafe {
            ok(DuplicateHandle(
                GetCurrentProcess(),
                handle,
                GetCurrentProcess(),
                &mut result,
                0,
                1,
                DUPLICATE_SAME_ACCESS,
            ))?;
        }
        owned(result)
    }
    fn pipe() -> io::Result<(OwnedHandle, OwnedHandle)> {
        let mut read = null_mut();
        let mut write = null_mut();
        unsafe {
            ok(CreatePipe(&mut read, &mut write, null(), 0))?;
        }
        Ok((owned(read)?, owned(write)?))
    }
    fn nul(access: u32) -> io::Result<OwnedHandle> {
        let name = wide(OsStr::new("NUL"))?;
        owned(unsafe {
            CreateFileW(
                name.as_ptr(),
                access,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                null(),
                OPEN_EXISTING,
                0,
                null_mut(),
            )
        })
    }
    // Python uses the Windows C-runtime argv convention. Always quote, including
    // empty args; double backslashes only before quotes and the closing quote.
    fn argument(value: &OsStr) -> io::Result<Vec<u16>> {
        let units = wide(value)?;
        let mut result = vec![b'"' as u16];
        let mut slashes = 0;
        for &unit in &units[..units.len() - 1] {
            if unit == b'\\' as u16 {
                slashes += 1;
                continue;
            }
            if unit == b'"' as u16 {
                result.extend(std::iter::repeat(b'\\' as u16).take(slashes * 2 + 1));
            } else {
                result.extend(std::iter::repeat(b'\\' as u16).take(slashes));
            }
            slashes = 0;
            result.push(unit);
        }
        result.extend(std::iter::repeat(b'\\' as u16).take(slashes * 2));
        result.push(b'"' as u16);
        Ok(result)
    }
    struct Attributes {
        storage: Vec<usize>,
        initialized: bool,
    }
    impl Attributes {
        fn new() -> io::Result<Self> {
            let mut bytes = 0;
            unsafe {
                InitializeProcThreadAttributeList(null_mut(), 2, 0, &mut bytes);
            }
            if bytes == 0 {
                return Err(io::Error::last_os_error());
            }
            let mut value = Self {
                storage: vec![0; bytes.div_ceil(std::mem::size_of::<usize>())],
                initialized: false,
            };
            unsafe {
                ok(InitializeProcThreadAttributeList(
                    value.ptr(),
                    2,
                    0,
                    &mut bytes,
                ))?;
            }
            value.initialized = true;
            Ok(value)
        }
        fn ptr(&mut self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
            self.storage.as_mut_ptr() as _
        }
        fn set(&mut self, key: u32, values: &[HANDLE]) -> io::Result<()> {
            unsafe {
                ok(UpdateProcThreadAttribute(
                    self.ptr(),
                    0,
                    key as usize,
                    values.as_ptr() as _,
                    std::mem::size_of_val(values),
                    null_mut(),
                    null(),
                ))
            }
        }
    }
    impl Drop for Attributes {
        fn drop(&mut self) {
            if self.initialized {
                unsafe {
                    DeleteProcThreadAttributeList(self.ptr());
                }
            }
        }
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    pub(crate) enum Shutdown {
        Orderly,
        Forced,
        Failed,
    }
    pub(crate) struct ExitOwner {
        job: OwnedHandle,
        process: OwnedHandle,
        event: OwnedHandle,
        started: OnceLock<Instant>,
        result: Mutex<Option<Shutdown>>,
    }
    impl ExitOwner {
        pub(crate) fn begin_stop(&self) -> io::Result<()> {
            self.started.get_or_init(Instant::now);
            unsafe { ok(SetEvent(raw(&self.event))) }
        }
        fn exit_status(&self) -> io::Result<Option<ExitStatus>> {
            match unsafe { WaitForSingleObject(raw(&self.process), 0) } {
                WAIT_TIMEOUT => Ok(None),
                WAIT_OBJECT_0 => {
                    let mut code = 0;
                    unsafe {
                        ok(GetExitCodeProcess(raw(&self.process), &mut code))?;
                    }
                    Ok(Some(ExitStatus::from_raw(code)))
                }
                _ => Err(io::Error::last_os_error()),
            }
        }
        fn active(&self) -> io::Result<u32> {
            let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { std::mem::zeroed() };
            unsafe {
                ok(QueryInformationJobObject(
                    raw(&self.job),
                    JobObjectBasicAccountingInformation,
                    &mut info as *mut _ as _,
                    std::mem::size_of_val(&info) as u32,
                    null_mut(),
                ))?;
            }
            Ok(info.ActiveProcesses)
        }
        pub(crate) fn force(&self) -> io::Result<()> {
            let result = unsafe { ok(TerminateJobObject(raw(&self.job), FORCE_EXIT)) };
            if result.is_err() {
                // A failed tree cleanup is still reported as failed. Retaining
                // the returned creation handle permits emergency leader cleanup
                // without a PID lookup or adopting any existing process.
                unsafe {
                    TerminateProcess(raw(&self.process), FORCE_EXIT);
                }
            }
            result
        }
        pub(crate) fn shutdown(&self) -> Shutdown {
            let signal = self.begin_stop();
            let mut result = self.result.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(value) = *result {
                return value;
            }
            let deadline = *self.started.get().unwrap() + Duration::from_secs(5);
            let mut leader_exited = None;
            while signal.is_ok() && Instant::now() < deadline {
                match self.exit_status() {
                    Ok(Some(status)) => {
                        if !status.success() {
                            break;
                        }
                        if self.active().ok() == Some(0) {
                            *result = Some(Shutdown::Orderly);
                            return Shutdown::Orderly;
                        }
                        // Process signaling and job accounting can settle in
                        // different kernel updates. Allow a short drain inside
                        // the original grace, then clean surviving descendants.
                        let exited = leader_exited.get_or_insert_with(Instant::now);
                        if exited.elapsed() >= Duration::from_millis(100) {
                            break;
                        }
                        std::thread::sleep(Duration::from_millis(5));
                    }
                    Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                    Err(_) => break,
                }
            }
            let forced = self.force().is_ok();
            let end = Instant::now() + Duration::from_secs(1);
            while Instant::now() < end {
                if self.exit_status().ok().flatten().is_some() && self.active().ok() == Some(0) {
                    let value = if forced {
                        Shutdown::Forced
                    } else {
                        Shutdown::Failed
                    };
                    *result = Some(value);
                    return value;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            *result = Some(Shutdown::Failed);
            Shutdown::Failed
        }
    }
    impl Drop for ExitOwner {
        fn drop(&mut self) {
            // Last noninherited job-handle close also handles a parent crash.
            // A normal drop still checks descendants if the leader already exited.
            let _ = self.shutdown();
        }
    }
    // This capability is independent of Session's request mutex. It is retained
    // only inside this desktop process; no job handle is inherited or reopened.
    pub(crate) struct DesktopExit {
        owner: Option<Arc<ExitOwner>>,
        requested: std::sync::atomic::AtomicBool,
        finished: std::sync::atomic::AtomicBool,
    }
    impl DesktopExit {
        pub(crate) fn new(owner: Option<Arc<ExitOwner>>) -> Self {
            Self {
                owner,
                requested: false.into(),
                finished: false.into(),
            }
        }
        pub(crate) fn stopping(&self) -> bool {
            self.requested.load(std::sync::atomic::Ordering::SeqCst)
        }
        pub(crate) fn finished(&self) -> bool {
            self.finished.load(std::sync::atomic::Ordering::SeqCst)
        }
        pub(crate) fn begin(&self) -> bool {
            if self
                .requested
                .swap(true, std::sync::atomic::Ordering::SeqCst)
            {
                return false;
            }
            if let Some(owner) = &self.owner {
                let _ = owner.begin_stop();
            }
            true
        }
        pub(crate) fn finish(&self) {
            if let Some(owner) = &self.owner {
                eprintln!("Native backend close: {:?}", owner.shutdown());
            }
            self.finished
                .store(true, std::sync::atomic::Ordering::SeqCst);
        }
        pub(crate) fn emergency(&self) {
            self.requested
                .store(true, std::sync::atomic::Ordering::SeqCst);
            if let Some(owner) = &self.owner {
                let _ = owner.force();
            }
        }
    }
    static DESKTOP: OnceLock<Arc<DesktopExit>> = OnceLock::new();
    pub(crate) fn install_exit(owner: Option<Arc<ExitOwner>>) -> io::Result<()> {
        DESKTOP
            .set(Arc::new(DesktopExit::new(owner)))
            .map_err(|_| io::Error::other("native exit owner already installed"))
    }
    pub(crate) fn desktop_exit() -> Option<Arc<DesktopExit>> {
        DESKTOP.get().cloned()
    }
    pub(crate) fn desktop_stopping() -> bool {
        DESKTOP.get().is_some_and(|v| v.stopping())
    }

    pub(crate) struct OwnedChild {
        owner: Arc<ExitOwner>,
        pub(crate) stdin: Option<File>,
        pub(crate) stdout: Option<File>,
    }
    impl OwnedChild {
        pub(crate) fn spawn(command: &mut Command, key_pipe: bool) -> io::Result<Self> {
            Self::spawn_inner(command, key_pipe, true)
        }
        fn spawn_inner(command: &mut Command, key_pipe: bool, lifecycle: bool) -> io::Result<Self> {
            let job = owned(unsafe { CreateJobObjectW(null(), null()) })?;
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            unsafe {
                ok(SetInformationJobObject(
                    raw(&job),
                    JobObjectExtendedLimitInformation,
                    &limits as *const _ as _,
                    std::mem::size_of_val(&limits) as u32,
                ))?;
            }
            let mut checked: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
            unsafe {
                ok(QueryInformationJobObject(
                    raw(&job),
                    JobObjectExtendedLimitInformation,
                    &mut checked as *mut _ as _,
                    std::mem::size_of_val(&checked) as u32,
                    null_mut(),
                ))?;
            }
            if checked.BasicLimitInformation.LimitFlags != JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE {
                return Err(io::Error::other("backend job limits unavailable"));
            }
            let event = owned(unsafe { CreateEventW(null(), 1, 0, null()) })?;
            let child_event = duplicate(raw(&event), SYNCHRONIZATION_SYNCHRONIZE, true)?;
            let (parent_stdin, child_stdin) = if key_pipe {
                let (read, write) = pipe()?;
                (Some(File::from(write)), inheritable(&read)?)
            } else {
                (None, inheritable(&nul(GENERIC_READ)?)?)
            };
            let (read, write) = pipe()?;
            let stdout = File::from(read);
            let child_stdout = inheritable(&write)?;
            drop(write);
            let stderr = unsafe { GetStdHandle(STD_ERROR_HANDLE) };
            let child_stderr = if stderr.is_null() || stderr == INVALID_HANDLE_VALUE {
                inheritable(&nul(GENERIC_WRITE)?)?
            } else {
                copy_inheritable(stderr).or_else(|_| inheritable(&nul(GENERIC_WRITE)?))?
            };
            let jobs = [raw(&job)];
            let handles = [
                raw(&child_stdin),
                raw(&child_stdout),
                raw(&child_stderr),
                raw(&child_event),
            ];
            let mut attrs = Attributes::new()?;
            attrs.set(PROC_THREAD_ATTRIBUTE_JOB_LIST, &jobs)?;
            attrs.set(PROC_THREAD_ATTRIBUTE_HANDLE_LIST, &handles)?;
            let application = wide(command.get_program())?;
            let mut args = vec![command.get_program().to_os_string()];
            args.extend(command.get_args().map(OsStr::to_os_string));
            if lifecycle {
                args.push("--lifecycle-event-handle".into());
                args.push((raw(&child_event) as usize).to_string().into());
            }
            let mut line = Vec::new();
            for arg in args {
                if !line.is_empty() {
                    line.push(32);
                }
                line.extend(argument(&arg)?);
            }
            line.push(0);
            if line.len() > 32767 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "backend arguments too long",
                ));
            }
            // Preserve original UTF-16 names/values, comparing keys through the
            // documented Windows ordinal case-insensitive API (no lossy UTF-8).
            let compare = |left: &OsStr, right: &OsStr| -> io::Result<std::cmp::Ordering> {
                let l = wide(left)?;
                let r = wide(right)?;
                match unsafe {
                    CompareStringOrdinal(
                        l.as_ptr(),
                        (l.len() - 1) as i32,
                        r.as_ptr(),
                        (r.len() - 1) as i32,
                        1,
                    )
                } {
                    1 => Ok(std::cmp::Ordering::Less),
                    2 => Ok(std::cmp::Ordering::Equal),
                    3 => Ok(std::cmp::Ordering::Greater),
                    _ => Err(io::Error::last_os_error()),
                }
            };
            let mut vars: Vec<(OsString, OsString)> = std::env::vars_os().collect();
            for (key, value) in command.get_envs() {
                let mut keep = Vec::new();
                for (k, v) in vars {
                    if compare(&k, key)? != std::cmp::Ordering::Equal {
                        keep.push((k, v));
                    }
                }
                if let Some(value) = value {
                    keep.push((key.into(), value.into()));
                }
                vars = keep;
            }
            let mut sort_error = None;
            vars.sort_by(|(left, _), (right, _)| {
                compare(left, right).unwrap_or_else(|error| {
                    sort_error = Some(error);
                    std::cmp::Ordering::Equal
                })
            });
            if let Some(error) = sort_error {
                return Err(error);
            }
            let mut env = Vec::new();
            for (key, value) in vars {
                let k = wide(&key)?;
                let v = wide(&value)?;
                env.extend_from_slice(&k[..k.len() - 1]);
                env.push(61);
                env.extend_from_slice(&v);
            }
            env.push(0);
            let cwd = wide(
                command
                    .get_current_dir()
                    .ok_or_else(|| {
                        io::Error::new(io::ErrorKind::InvalidInput, "backend cwd missing")
                    })?
                    .as_os_str(),
            )?;
            let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
            startup.StartupInfo.cb = std::mem::size_of_val(&startup) as u32;
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
            startup.StartupInfo.hStdInput = raw(&child_stdin);
            startup.StartupInfo.hStdOutput = raw(&child_stdout);
            startup.StartupInfo.hStdError = raw(&child_stderr);
            startup.lpAttributeList = attrs.ptr();
            let mut process: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
            unsafe {
                ok(CreateProcessW(
                    application.as_ptr(),
                    line.as_mut_ptr(),
                    null(),
                    null(),
                    1,
                    EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                    env.as_ptr() as *const c_void,
                    cwd.as_ptr(),
                    &startup.StartupInfo,
                    &mut process,
                ))?;
            }
            let process_handle = owned(process.hProcess)?;
            drop(owned(process.hThread)?);
            let owner = Arc::new(ExitOwner {
                job,
                process: process_handle,
                event,
                started: OnceLock::new(),
                result: Mutex::new(None),
            });
            let mut member = 0;
            if unsafe { IsProcessInJob(raw(&owner.process), raw(&owner.job), &mut member) } == 0
                || member == 0
            {
                let _ = owner.force();
                unsafe {
                    TerminateProcess(raw(&owner.process), FORCE_EXIT);
                }
                return Err(io::Error::other("backend creation-time job unavailable"));
            }
            Ok(Self {
                owner,
                stdin: parent_stdin,
                stdout: Some(stdout),
            })
        }
        pub(crate) fn exit_owner(&self) -> Arc<ExitOwner> {
            self.owner.clone()
        }

        pub(crate) fn kill(&mut self) -> io::Result<()> {
            self.owner.force()
        }
        pub(crate) fn wait(&mut self) -> io::Result<ExitStatus> {
            unsafe {
                if WaitForSingleObject(raw(&self.owner.process), 1000) != WAIT_OBJECT_0 {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "backend join timed out",
                    ));
                }
            }
            self.owner
                .exit_status()?
                .ok_or_else(|| io::Error::other("backend still running"))
        }
        pub(crate) fn readiness_line(&mut self, timeout: Duration) -> io::Result<String> {
            // No detached blocking reader exists on Windows. This sole pipe
            // reader consumes only bytes reported available, under one deadline.
            let mut file = self
                .stdout
                .take()
                .ok_or_else(|| io::Error::other("readiness pipe missing"))?;
            let until = Instant::now() + timeout;
            let mut line = Vec::new();
            while Instant::now() < until && line.len() < 4097 {
                let mut available = 0;
                unsafe {
                    ok(PeekNamedPipe(
                        file.as_raw_handle() as _,
                        null_mut(),
                        0,
                        null_mut(),
                        &mut available,
                        null_mut(),
                    ))?;
                }
                if available == 0 {
                    std::thread::sleep(Duration::from_millis(5));
                    continue;
                }
                let mut byte = [0];
                if file.read(&mut byte)? == 0 {
                    break;
                }
                line.push(byte[0]);
                if byte[0] == b'\n' {
                    return String::from_utf8(line)
                        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e));
                }
            }
            Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "backend readiness unavailable",
            ))
        }
    }
    impl Drop for OwnedChild {
        fn drop(&mut self) {
            let _ = self.owner.shutdown();
        }
    }

}
#[cfg(windows)]
pub(crate) use windows::{desktop_exit, desktop_stopping, install_exit, ExitOwner, OwnedChild};
