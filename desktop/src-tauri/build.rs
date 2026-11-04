fn main() {
    println!("cargo:rerun-if-env-changed=KEJTU_PACKAGE_KIND");
    let package_kind = std::env::var("KEJTU_PACKAGE_KIND").unwrap_or_else(|_| "development".into());
    assert!(
        matches!(
            package_kind.as_str(),
            "development" | "windows-test-portable"
        ),
        "unknown package kind"
    );
    println!("cargo:rustc-env=KEJTU_PACKAGE_KIND={package_kind}");
    let manifest = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let candidate = manifest.parent().unwrap().parent().unwrap();
    println!("cargo:rerun-if-changed={}", candidate.join("src").display());
    println!(
        "cargo:rerun-if-changed={}",
        candidate.join("contracts/native-v1.json").display()
    );
    println!("cargo:rerun-if-env-changed=KEJTU_BUILD_PYTHON");
    let python = std::env::var_os("KEJTU_BUILD_PYTHON")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            candidate.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            })
        });
    assert!(
        python.is_absolute(),
        "build Python must be an absolute path"
    );
    // Isolated/no-site mode ignores installed/user packages and CWD. Pin this candidate
    // before importing the existing fingerprint owner, never an installed copy.
    let output = std::process::Command::new(python)
        .current_dir(candidate)
        .args(["-I", "-S", "-B", "-c", "import sys; from pathlib import Path; root=Path(sys.argv[1]).resolve(); sys.path.insert(0, str(root/'src')); from kejtu_next.interfaces import native_server; assert Path(native_server.__file__).resolve()==root/'src/kejtu_next/interfaces/native_server.py', 'foreign fingerprint owner'; print(native_server.build_fingerprint())"])
        .arg(candidate)
        .output()
        .expect("candidate build identity unavailable; prepare .venv or set KEJTU_BUILD_PYTHON");
    assert!(
        output.status.success(),
        "candidate identity computation failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let identity = String::from_utf8(output.stdout).unwrap();
    let identity = identity.trim();
    assert!(
        identity.len() == 64
            && identity
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    );
    println!("cargo:rustc-env=R013_BUILD_SHA256={identity}");
    tauri_build::build()
}
