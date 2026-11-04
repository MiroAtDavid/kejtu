//! Private native records: atomic publication and explicit post-publication uncertainty.
//! Windows uses local NTFS write-through handles, never a pretend directory fsync.
use std::fs::{self, File};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(1);

#[derive(Debug)]
pub(crate) struct PersistError {
    pub(crate) published: bool,
    source: io::Error,
}
impl std::fmt::Display for PersistError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(if self.published {
            "private native record published; durability is uncertain"
        } else {
            "private native record unavailable"
        })
    }
}
impl std::error::Error for PersistError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(&self.source)
    }
}
fn failure(source: io::Error, published: bool) -> PersistError {
    PersistError { published, source }
}
fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, "unsafe private record path")
}
fn temporary(path: &Path) -> io::Result<PathBuf> {
    let leaf = path.file_name().ok_or_else(invalid)?.to_string_lossy();
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|_| invalid())?
        .as_nanos();
    Ok(path.with_file_name(format!(
        ".{leaf}.{}.{nonce}.{}.tmp",
        std::process::id(),
        TEMP_COUNTER.fetch_add(1, Ordering::Relaxed)
    )))
}

pub(crate) fn write(path: &Path, raw: &[u8]) -> Result<(), PersistError> {
    write_checked(path, raw, || Ok(()))
}
fn write_checked(
    path: &Path,
    raw: &[u8],
    after_rename: impl FnOnce() -> io::Result<()>,
) -> Result<(), PersistError> {
    let temp = temporary(path).map_err(|e| failure(e, false))?;
    let guard = platform::parents(path).map_err(|e| failure(e, false))?;
    platform::check_leaf(path).map_err(|e| failure(e, false))?;
    let mut file = platform::create(&temp).map_err(|e| failure(e, false))?;
    let mut published = false;
    let result = (|| {
        file.write_all(raw)?;
        platform::flush(&file)?;
        platform::rename(&file, &temp, path)?;
        // Publication is authoritative even when the following persistence check fails.
        published = true;
        after_rename()?;
        platform::finish(&file, path)
    })();
    drop(file);
    if !published {
        let _ = fs::remove_file(&temp);
    }
    drop(guard);
    result.map_err(|e| failure(e, published))
}

pub(crate) fn clear(path: &Path, cleared: &Path) -> Result<(), PersistError> {
    platform::clear(path, cleared, || Ok(()))
}

#[cfg(unix)]
mod platform {
    use super::*;
    use std::os::unix::fs::OpenOptionsExt;
    pub(super) struct Parents;
    pub(super) fn parents(_: &Path) -> io::Result<Parents> {
        Ok(Parents)
    }
    pub(super) fn check_leaf(path: &Path) -> io::Result<()> {
        match fs::symlink_metadata(path) {
            Ok(info) if info.is_file() => Ok(()),
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(e),
            _ => Err(invalid()),
        }
    }
    pub(super) fn create(path: &Path) -> io::Result<File> {
        fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)
    }
    pub(super) fn flush(file: &File) -> io::Result<()> {
        file.sync_all()
    }
    pub(super) fn rename(_: &File, from: &Path, to: &Path) -> io::Result<()> {
        fs::rename(from, to)
    }
    pub(super) fn finish(_: &File, path: &Path) -> io::Result<()> {
        File::open(path.parent().ok_or_else(invalid)?)?.sync_all()
    }
    pub(super) fn clear(
        path: &Path,
        _: &Path,
        after_rename: impl FnOnce() -> io::Result<()>,
    ) -> Result<(), PersistError> {
        fs::remove_file(path).map_err(|e| failure(e, false))?;
        after_rename().map_err(|e| failure(e, true))?;
        finish(
            &File::open(path.parent().ok_or_else(|| failure(invalid(), true))?)
                .map_err(|e| failure(e, true))?,
            path,
        )
        .map_err(|e| failure(e, true))
    }
}

#[cfg(windows)]
mod platform {
    use super::*;
    use std::ffi::c_void;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle};
    use std::path::{Component, Prefix};
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::{
        CloseHandle, LocalFree, GENERIC_READ, GENERIC_WRITE, HANDLE, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Security::Authorization::{
        ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
    };
    use windows_sys::Win32::Security::{
        GetTokenInformation, TokenUser, SECURITY_ATTRIBUTES, TOKEN_QUERY, TOKEN_USER,
    };
    use windows_sys::Win32::Storage::FileSystem::*;
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    use windows_sys::Win32::System::WindowsProgramming::{DRIVE_FIXED, DRIVE_REMOVABLE};

    struct Handle(HANDLE);
    impl Drop for Handle {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
    struct Local(*mut c_void);
    impl Drop for Local {
        fn drop(&mut self) {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
    fn wide(path: &Path) -> io::Result<Vec<u16>> {
        let mut value: Vec<u16> = path.as_os_str().encode_wide().collect();
        if value.contains(&0) {
            return Err(invalid());
        }
        value.push(0);
        Ok(value)
    }
    fn ok(value: i32) -> io::Result<()> {
        if value == 0 {
            Err(io::Error::last_os_error())
        } else {
            Ok(())
        }
    }
    fn attributes(handle: HANDLE, directory: bool) -> io::Result<()> {
        let mut info: FILE_ATTRIBUTE_TAG_INFO = unsafe { std::mem::zeroed() };
        unsafe {
            ok(GetFileInformationByHandleEx(
                handle,
                FileAttributeTagInfo,
                &mut info as *mut _ as _,
                std::mem::size_of_val(&info) as u32,
            ))?;
        }
        if info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
            || (info.FileAttributes & FILE_ATTRIBUTE_DIRECTORY != 0) != directory
            || unsafe { GetFileType(handle) } != FILE_TYPE_DISK
        {
            return Err(invalid());
        }
        Ok(())
    }
    fn open(path: &Path, access: u32, share: u32, flags: u32) -> io::Result<Handle> {
        let path = wide(path)?;
        let handle = unsafe {
            CreateFileW(
                path.as_ptr(),
                access,
                share,
                null(),
                OPEN_EXISTING,
                flags | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
                null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        Ok(Handle(handle))
    }
    pub(super) struct Parents {
        _handles: Vec<Handle>,
    }
    pub(super) fn parents(path: &Path) -> io::Result<Parents> {
        if !path.is_absolute()
            || path
                .components()
                .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
        {
            return Err(invalid());
        }
        let drive = match path.components().next() {
            Some(Component::Prefix(prefix)) => match prefix.kind() {
                Prefix::Disk(letter) | Prefix::VerbatimDisk(letter) => letter,
                _ => return Err(invalid()),
            },
            _ => return Err(invalid()),
        };
        let root = [drive as u16, b':' as u16, b'\\' as u16, 0];
        let kind = unsafe { GetDriveTypeW(root.as_ptr()) };
        if kind != DRIVE_FIXED && kind != DRIVE_REMOVABLE {
            return Err(invalid());
        }
        let parent = path.parent().ok_or_else(invalid)?;
        let mut handles = Vec::new();
        for directory in parent.ancestors().collect::<Vec<_>>().into_iter().rev() {
            // Hold every checked ancestor against data/reparse mutation and rename/deletion.
            let handle = open(directory, 0, FILE_SHARE_READ, 0)?;
            attributes(handle.0, true)?;
            handles.push(handle);
        }
        let mut fs_name = [0u16; 32];
        unsafe {
            ok(GetVolumeInformationByHandleW(
                handles.last().ok_or_else(invalid)?.0,
                null_mut(),
                0,
                null_mut(),
                null_mut(),
                null_mut(),
                fs_name.as_mut_ptr(),
                fs_name.len() as u32,
            ))?;
        }
        let end = fs_name.iter().position(|c| *c == 0).ok_or_else(invalid)?;
        if String::from_utf16_lossy(&fs_name[..end]) != "NTFS" {
            return Err(invalid());
        }
        Ok(Parents { _handles: handles })
    }
    pub(super) fn check_leaf(path: &Path) -> io::Result<()> {
        match open(
            path,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            0,
        ) {
            Ok(handle) => attributes(handle.0, false),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }
    fn descriptor() -> io::Result<Local> {
        let mut token = null_mut();
        unsafe {
            ok(OpenProcessToken(
                GetCurrentProcess(),
                TOKEN_QUERY,
                &mut token,
            ))?;
        }
        let token = Handle(token);
        let mut needed = 0;
        unsafe {
            GetTokenInformation(token.0, TokenUser, null_mut(), 0, &mut needed);
        }
        if needed == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut data = vec![
            0usize;
            (needed as usize + std::mem::size_of::<usize>() - 1)
                / std::mem::size_of::<usize>()
        ];
        unsafe {
            ok(GetTokenInformation(
                token.0,
                TokenUser,
                data.as_mut_ptr() as _,
                needed,
                &mut needed,
            ))?;
        }
        let user = unsafe { &*(data.as_ptr() as *const TOKEN_USER) };
        let mut sid = null_mut();
        unsafe {
            ok(ConvertSidToStringSidW(user.User.Sid, &mut sid))?;
        }
        let sid_guard = Local(sid as _);
        let mut count = 0;
        unsafe {
            while *sid.add(count) != 0 {
                count += 1;
            }
        }
        let sid_text = unsafe { String::from_utf16_lossy(std::slice::from_raw_parts(sid, count)) };
        drop(sid_guard);
        // Only the current user and SYSTEM can access the newly created record.
        // This is creation-time protection, never a chmod/ACL rewrite of user folders.
        let sddl: Vec<u16> = format!("O:{sid_text}D:P(A;;FA;;;{sid_text})(A;;FA;;;SY)\0")
            .encode_utf16()
            .collect();
        let mut result = null_mut();
        unsafe {
            ok(ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut result,
                null_mut(),
            ))?;
        }
        Ok(Local(result))
    }

    pub(super) fn create(path: &Path) -> io::Result<File> {
        let path = wide(path)?;
        let descriptor = descriptor()?;
        let security = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        let handle = unsafe {
            CreateFileW(
                path.as_ptr(),
                GENERIC_READ | GENERIC_WRITE | DELETE,
                0,
                &security,
                CREATE_NEW,
                FILE_FLAG_WRITE_THROUGH | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
                null_mut(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(io::Error::last_os_error());
        }
        let file = unsafe { File::from_raw_handle(handle as _) };
        attributes(handle, false)?;
        Ok(file)
    }
    pub(super) fn flush(file: &File) -> io::Result<()> {
        unsafe { ok(FlushFileBuffers(file.as_raw_handle() as _)) }
    }
    pub(super) fn rename(file: &File, _: &Path, to: &Path) -> io::Result<()> {
        let name = wide(to)?;
        let bytes = (name.len() - 1) * 2;
        // Aligned allocation plus explicit trailing UTF-16 NUL. FileNameLength excludes it.
        let offset = std::mem::offset_of!(FILE_RENAME_INFO, FileName);
        let mut buffer = vec![0u64; (offset + bytes + 2 + 7) / 8];
        unsafe {
            let info = buffer.as_mut_ptr() as *mut FILE_RENAME_INFO;
            (*info).Anonymous.ReplaceIfExists = true;
            (*info).RootDirectory = null_mut();
            (*info).FileNameLength = bytes as u32;
            std::ptr::copy_nonoverlapping(name.as_ptr(), (*info).FileName.as_mut_ptr(), name.len());
            ok(SetFileInformationByHandle(
                file.as_raw_handle() as _,
                FileRenameInfo,
                info as _,
                (offset + bytes + 2) as u32,
            ))
        }
    }
    pub(super) fn finish(file: &File, _: &Path) -> io::Result<()> {
        flush(file)
    }
    pub(super) fn clear(
        path: &Path,
        cleared: &Path,
        after_rename: impl FnOnce() -> io::Result<()>,
    ) -> Result<(), PersistError> {
        let _guard = parents(path).map_err(|e| failure(e, false))?;
        if path.parent() != cleared.parent() {
            return Err(failure(invalid(), false));
        }
        check_leaf(cleared).map_err(|e| failure(e, false))?;
        let handle = open(
            path,
            GENERIC_READ | GENERIC_WRITE | DELETE,
            0,
            FILE_FLAG_WRITE_THROUGH,
        )
        .map_err(|e| failure(e, false))?;
        attributes(handle.0, false).map_err(|e| failure(e, false))?;
        let file = unsafe { File::from_raw_handle(handle.0 as _) };
        std::mem::forget(handle);
        flush(&file)
            .and_then(|_| rename(&file, path, cleared))
            .map_err(|e| failure(e, false))?;
        // Keep the same intent bytes privately at the cleared path; pending readers see absence.
        after_rename().map_err(|e| failure(e, true))?;
        flush(&file).map_err(|e| failure(e, true))
    }
}


