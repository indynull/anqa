//! Unix summon socket for compositor binds (Sway/Wayland).
//!
//! In-process global-hotkey is X11-only on Linux. On Wayland the product path
//! is: keep a long-lived HUD, then ``show`` / ``hide`` / ``toggle`` over a
//! per-user runtime Unix socket (same layout as the control plane).
//!
//! Commands are one line: ``show``, ``hide``, ``toggle``, or
//! ``show``/``toggle`` plus an xdg-activation token (optional trailing
//! newline). Clients: ``anqa-hud --show`` / ``anqa desktop --toggle``.
//! ``--toggle`` forwards ``XDG_ACTIVATION_TOKEN`` (and unsets
//! ``DESKTOP_STARTUP_ID``) so the long-lived HUD can activate its surface.

#[cfg(unix)]
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, Receiver, RecvError, SyncSender};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use thiserror::Error;

/// Env override for the summon socket path.
pub const SOCKET_ENV: &str = "ANQA_HUD_SUMMON_SOCKET";

/// Operator action for the iced loop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SummonAction {
    Show,
    Hide,
    Toggle,
}

/// One summon request: verb, optional xdg-activation token, optional session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SummonRequest {
    pub action: SummonAction,
    pub token: Option<String>,
    pub session_id: Option<String>,
}

impl SummonRequest {
    pub fn new(action: SummonAction) -> Self {
        Self {
            action,
            token: None,
            session_id: None,
        }
    }

    /// Show the overlay and open *session_id* (notify click / ``--open``).
    pub fn open(session_id: impl Into<String>) -> Self {
        Self {
            action: SummonAction::Show,
            token: None,
            session_id: sanitize_session_id(&session_id.into()),
        }
    }
}

#[derive(Debug, Error)]
pub enum SummonError {
    #[error("summon socket not available on this platform")]
    Unsupported,
    #[error("summon socket path could not be resolved")]
    NoPath,
    #[error("HUD summon socket not accepting ({0})")]
    NotRunning(String),
    #[error("HUD summon socket already in use ({0})")]
    AlreadyRunning(String),
    #[error("{0}")]
    Io(#[from] std::io::Error),
    #[error("{0}")]
    Other(String),
}

/// Holds the listener thread and bound path for process lifetime.
pub struct SummonServer {
    path: PathBuf,
    #[allow(dead_code)]
    join: Option<thread::JoinHandle<()>>,
}

impl Drop for SummonServer {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

/// Parse a single command line (trimmed, case-insensitive).
pub fn parse_command(raw: &str) -> Option<SummonAction> {
    parse_request(raw).map(|r| r.action)
}

/// Parse ``show`` / ``hide`` / ``toggle`` / ``open <sessionId>`` and an optional token.
pub fn parse_request(raw: &str) -> Option<SummonRequest> {
    let line = raw.trim();
    if line.is_empty() {
        return None;
    }
    let mut parts = line.splitn(2, char::is_whitespace);
    let verb = parts.next()?.to_ascii_lowercase();
    let rest = parts.next().map(str::trim).filter(|t| !t.is_empty());
    if verb == "open" {
        let rest = rest?;
        let mut bits = rest.splitn(2, char::is_whitespace);
        let sid = sanitize_session_id(bits.next()?)?;
        let token = bits
            .next()
            .map(str::trim)
            .filter(|t| !t.is_empty())
            .and_then(sanitize_token);
        return Some(SummonRequest {
            action: SummonAction::Show,
            token,
            session_id: Some(sid),
        });
    }
    let action = match verb.as_str() {
        "show" => SummonAction::Show,
        "hide" => SummonAction::Hide,
        "toggle" => SummonAction::Toggle,
        _ => return None,
    };
    let token = rest.and_then(sanitize_token);
    let token = match action {
        SummonAction::Hide => None,
        _ => token,
    };
    Some(SummonRequest {
        action,
        token,
        session_id: None,
    })
}

/// Session id after trim: 1..=256 bytes, no whitespace or CR/LF.
pub fn sanitize_session_id(raw: &str) -> Option<String> {
    let t = raw.trim();
    if t.is_empty() || t.len() > 256 {
        return None;
    }
    if t.chars().any(|c| c.is_whitespace() || c == '\n' || c == '\r') {
        return None;
    }
    Some(t.to_string())
}

/// Token after trim: 1..=512 bytes, no CR/LF. Empty or oversize is absent.
pub fn sanitize_token(raw: &str) -> Option<String> {
    let t = raw.trim();
    if t.is_empty() || t.len() > 512 || t.contains('\n') || t.contains('\r') {
        return None;
    }
    Some(t.to_string())
}

/// Read ``XDG_ACTIVATION_TOKEN`` and unset it and ``DESKTOP_STARTUP_ID``.
pub fn take_env_token() -> Option<String> {
    let raw = std::env::var("XDG_ACTIVATION_TOKEN").ok();
    std::env::remove_var("XDG_ACTIVATION_TOKEN");
    std::env::remove_var("DESKTOP_STARTUP_ID");
    raw.as_deref().and_then(sanitize_token)
}

/// Wire form for *action* (one word, no newline).
pub fn command_word(action: SummonAction) -> &'static str {
    match action {
        SummonAction::Show => "show",
        SummonAction::Hide => "hide",
        SummonAction::Toggle => "toggle",
    }
}

/// Default path: ``$XDG_RUNTIME_DIR/anqa/hud-summon.sock``, or
/// ``~/.anqa/run/hud-summon.sock`` when runtime dir is unset.
pub fn default_socket_path() -> Option<PathBuf> {
    if let Ok(raw) = std::env::var(SOCKET_ENV) {
        let t = raw.trim();
        if !t.is_empty() {
            return Some(PathBuf::from(t));
        }
    }
    if let Ok(runtime) = std::env::var("XDG_RUNTIME_DIR") {
        let t = runtime.trim();
        if !t.is_empty() {
            return Some(Path::new(t).join("anqa").join("hud-summon.sock"));
        }
    }
    let home = std::env::var_os("HOME")?;
    Some(
        PathBuf::from(home)
            .join(".anqa")
            .join("run")
            .join("hud-summon.sock"),
    )
}

/// True when a HUD already owns the default summon socket.
pub fn already_running() -> bool {
    default_socket_path().is_some_and(|p| socket_accepts(&p))
}

/// True when a listener is bound (connect succeeds).
pub fn socket_accepts(path: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::net::UnixStream::connect(path).is_ok()
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        false
    }
}

/// What `--show` / `--hide` / `--toggle` should do after talking to the socket.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SummonCli {
    /// Command delivered, or hide with nothing running.
    Done,
    /// Start a new HUD and show the overlay.
    StartShown,
}

/// True when the socket is absent so a compositor bind cannot talk to a HUD.
pub fn is_summon_miss(err: &SummonError) -> bool {
    matches!(
        err,
        SummonError::NotRunning(_) | SummonError::NoPath | SummonError::Unsupported
    )
}

/// Plan the binary's next step after [`send_command`].
///
/// Hide with no listener exits successfully. Show and toggle start a HUD.
pub fn plan_summon_cli(
    action: SummonAction,
    result: Result<(), SummonError>,
) -> Result<SummonCli, SummonError> {
    match result {
        Ok(()) => Ok(SummonCli::Done),
        Err(err) if is_summon_miss(&err) && matches!(action, SummonAction::Hide) => {
            Ok(SummonCli::Done)
        }
        Err(err) if is_summon_miss(&err) => Ok(SummonCli::StartShown),
        Err(err) => Err(err),
    }
}

/// Send one summon command to a running HUD.
pub fn send_command(action: SummonAction) -> Result<(), SummonError> {
    let token = match action {
        SummonAction::Hide => {
            let _ = take_env_token();
            None
        }
        _ => take_env_token(),
    };
    send_request(SummonRequest {
        action,
        token,
        session_id: None,
    })
}

/// Send a parsed request to the default socket.
pub fn send_request(req: SummonRequest) -> Result<(), SummonError> {
    #[cfg(unix)]
    {
        let path = default_socket_path().ok_or(SummonError::NoPath)?;
        send_request_to(&path, &req)
    }
    #[cfg(not(unix))]
    {
        let _ = req;
        Err(SummonError::Unsupported)
    }
}

/// Send *action* to *path* (no env token).
pub fn send_command_to(path: &Path, action: SummonAction) -> Result<(), SummonError> {
    send_request_to(path, &SummonRequest::new(action))
}

/// Write one wire line to *path*.
pub fn send_request_to(path: &Path, req: &SummonRequest) -> Result<(), SummonError> {
    #[cfg(unix)]
    {
        use std::os::unix::net::UnixStream;
        let mut stream = UnixStream::connect(path)
            .map_err(|err| SummonError::NotRunning(format!("{}: {err}", path.display())))?;
        let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
        let line = encode_request(req);
        stream.write_all(line.as_bytes())?;
        stream.flush()?;
        Ok(())
    }
    #[cfg(not(unix))]
    {
        let _ = (path, req);
        Err(SummonError::Unsupported)
    }
}

/// Canonical wire: ``verb``, ``verb token``, ``open sid``, or ``open sid token``.
pub fn encode_request(req: &SummonRequest) -> String {
    if let Some(sid) = req.session_id.as_deref().and_then(sanitize_session_id) {
        return match req.token.as_deref().and_then(sanitize_token) {
            Some(tok) => format!("open {sid} {tok}\n"),
            None => format!("open {sid}\n"),
        };
    }
    match req.action {
        SummonAction::Hide => format!("{}\n", command_word(req.action)),
        _ => match req.token.as_deref().and_then(sanitize_token) {
            Some(tok) => format!("{} {tok}\n", command_word(req.action)),
            None => format!("{}\n", command_word(req.action)),
        },
    }
}

/// Bind the summon socket and start the accept thread.
pub fn install() -> Result<SummonServer, SummonError> {
    #[cfg(unix)]
    {
        install_unix()
    }
    #[cfg(not(unix))]
    {
        Err(SummonError::Unsupported)
    }
}

/// Block until the next summon request (iced subscription).
pub fn recv_action() -> Result<SummonRequest, RecvError> {
    loop {
        let outcome = {
            let guard = action_pair().1.lock().expect("summon action mutex");
            guard.try_recv()
        };
        match outcome {
            Ok(action) => return Ok(action),
            Err(std::sync::mpsc::TryRecvError::Disconnected) => return Err(RecvError),
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                thread::sleep(Duration::from_millis(25));
            }
        }
    }
}

fn action_pair() -> &'static (SyncSender<SummonRequest>, Mutex<Receiver<SummonRequest>>) {
    static PAIR: OnceLock<(SyncSender<SummonRequest>, Mutex<Receiver<SummonRequest>>)> =
        OnceLock::new();
    PAIR.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel(16);
        (tx, Mutex::new(rx))
    })
}

#[cfg(unix)]
fn action_sender() -> SyncSender<SummonRequest> {
    action_pair().0.clone()
}

/// Probe before unlink: a live HUD keeps the inode; only a stale path is removed.
#[cfg(unix)]
fn prepare_bind_path(path: &Path) -> Result<(), SummonError> {
    if socket_accepts(path) {
        return Err(SummonError::AlreadyRunning(path.display().to_string()));
    }
    if path.exists() {
        let _ = std::fs::remove_file(path);
    }
    Ok(())
}

#[cfg(unix)]
fn install_unix() -> Result<SummonServer, SummonError> {
    use std::os::unix::net::UnixListener;

    let path = default_socket_path().ok_or(SummonError::NoPath)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    prepare_bind_path(&path)?;
    let listener = UnixListener::bind(&path).map_err(|err| {
        if err.kind() == std::io::ErrorKind::AddrInUse {
            SummonError::AlreadyRunning(path.display().to_string())
        } else {
            SummonError::Io(err)
        }
    })?;
    // Restrict to the user (runtime dir is usually already 0700).
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    }
    let _ = action_sender();
    let path_log = path.clone();
    let join = thread::Builder::new()
        .name("anqa-hud-summon".into())
        .spawn(move || accept_loop(listener))
        .map_err(|err| SummonError::Other(format!("spawn summon thread: {err}")))?;
    crate::log::info(&format!("summon socket {}", path_log.display()));
    Ok(SummonServer {
        path,
        join: Some(join),
    })
}

#[cfg(unix)]
fn accept_loop(listener: std::os::unix::net::UnixListener) {
    let tx = action_sender();
    loop {
        let Ok((stream, _)) = listener.accept() else {
            thread::sleep(Duration::from_millis(50));
            continue;
        };
        if let Some(req) = read_action(stream) {
            if tx.send(req).is_err() {
                break;
            }
        }
    }
}

#[cfg(unix)]
fn read_action(stream: std::os::unix::net::UnixStream) -> Option<SummonRequest> {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line).ok()?;
    parse_request(&line)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_command_words() {
        assert_eq!(parse_command("show"), Some(SummonAction::Show));
        assert_eq!(parse_command(" HIDE\n"), Some(SummonAction::Hide));
        assert_eq!(parse_command("Toggle"), Some(SummonAction::Toggle));
        assert_eq!(parse_command("quit"), None);
        assert_eq!(parse_command(""), None);
    }

    #[test]
    fn plan_summon_cli_starts_when_show_misses() {
        let miss = SummonError::NotRunning("gone".into());
        assert_eq!(
            plan_summon_cli(SummonAction::Show, Err(miss)).unwrap(),
            SummonCli::StartShown
        );
        assert_eq!(
            plan_summon_cli(SummonAction::Toggle, Err(SummonError::NoPath)).unwrap(),
            SummonCli::StartShown
        );
        assert_eq!(
            plan_summon_cli(SummonAction::Hide, Err(SummonError::Unsupported)).unwrap(),
            SummonCli::Done
        );
        assert_eq!(
            plan_summon_cli(SummonAction::Show, Ok(())).unwrap(),
            SummonCli::Done
        );
        assert!(plan_summon_cli(SummonAction::Show, Err(SummonError::Other("x".into()))).is_err());
    }

    #[test]
    fn command_word_round_trip() {
        for action in [SummonAction::Show, SummonAction::Hide, SummonAction::Toggle] {
            assert_eq!(parse_command(command_word(action)), Some(action));
        }
    }

    #[test]
    fn default_path_uses_runtime_or_home() {
        // Env may or may not be set in CI; only require a path with the socket name.
        if let Some(p) = default_socket_path() {
            assert!(p.file_name().is_some_and(|n| n == "hud-summon.sock"));
        }
    }

    #[cfg(unix)]
    #[test]
    fn send_round_trip_on_temp_socket() {
        use std::os::unix::net::UnixListener;
        use std::sync::mpsc;

        let dir = std::env::temp_dir().join(format!("anqa-hud-summon-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("hud-summon.sock");
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).expect("bind");
        let (tx, rx) = mpsc::sync_channel(1);
        let path_server = path.clone();
        let handle = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept");
            let action = read_action(stream).expect("action");
            tx.send(action).unwrap();
            let _ = std::fs::remove_file(&path_server);
        });
        send_command_to(&path, SummonAction::Toggle).expect("send");
        let got = rx.recv_timeout(Duration::from_secs(2)).expect("recv");
        assert_eq!(got, SummonRequest::new(SummonAction::Toggle));
        handle.join().unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn parse_request_keeps_token_on_show_and_toggle() {
        assert_eq!(
            parse_request("toggle abc.def"),
            Some(SummonRequest {
                action: SummonAction::Toggle,
                token: Some("abc.def".into()),
                session_id: None,
            })
        );
        assert_eq!(
            parse_request("show  tok-1 "),
            Some(SummonRequest {
                action: SummonAction::Show,
                token: Some("tok-1".into()),
                session_id: None,
            })
        );
    }

    #[test]
    fn parse_request_strips_token_on_hide() {
        assert_eq!(
            parse_request("hide leftover"),
            Some(SummonRequest::new(SummonAction::Hide))
        );
    }

    #[test]
    fn sanitize_token_rejects_empty_newline_and_oversize() {
        assert_eq!(sanitize_token("  "), None);
        assert_eq!(sanitize_token("a\nb"), None);
        assert_eq!(sanitize_token("a\rb"), None);
        assert_eq!(sanitize_token(&"x".repeat(513)), None);
        assert_eq!(sanitize_token("ok"), Some("ok".into()));
    }

    #[test]
    fn encode_request_one_line() {
        assert_eq!(
            encode_request(&SummonRequest::new(SummonAction::Toggle)),
            "toggle\n"
        );
        assert_eq!(
            encode_request(&SummonRequest {
                action: SummonAction::Show,
                token: Some("t1".into()),
                session_id: None,
            }),
            "show t1\n"
        );
        assert_eq!(
            encode_request(&SummonRequest {
                action: SummonAction::Toggle,
                token: Some("t2".into()),
                session_id: None,
            }),
            "toggle t2\n"
        );
        assert_eq!(
            encode_request(&SummonRequest {
                action: SummonAction::Toggle,
                token: Some("a\nb".into()),
                session_id: None,
            }),
            "toggle\n"
        );
        assert_eq!(
            encode_request(&SummonRequest {
                action: SummonAction::Hide,
                token: Some("ignored".into()),
                session_id: None,
            }),
            "hide\n"
        );
        assert_eq!(
            encode_request(&SummonRequest::open("sess-1")),
            "open sess-1\n"
        );
    }

    #[test]
    fn parse_request_open_session() {
        assert_eq!(
            parse_request("open sess-1"),
            Some(SummonRequest::open("sess-1"))
        );
        assert_eq!(
            parse_request("OPEN  grok:abc  tok-9"),
            Some(SummonRequest {
                action: SummonAction::Show,
                token: Some("tok-9".into()),
                session_id: Some("grok:abc".into()),
            })
        );
        assert_eq!(parse_request("open"), None);
        assert_eq!(parse_request("open   "), None);
        assert_eq!(sanitize_session_id("a b"), None);
        assert_eq!(sanitize_session_id(&"x".repeat(257)), None);
    }

    #[cfg(unix)]
    #[test]
    fn send_request_round_trip_with_token() {
        use std::os::unix::net::UnixListener;
        use std::sync::mpsc;

        let dir = std::env::temp_dir().join(format!("anqa-hud-summon-tok-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("hud-summon.sock");
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).expect("bind");
        let (tx, rx) = mpsc::sync_channel(1);
        let path_server = path.clone();
        let handle = thread::spawn(move || {
            let (stream, _) = listener.accept().expect("accept");
            let req = read_action(stream).expect("request");
            tx.send(req).unwrap();
            let _ = std::fs::remove_file(&path_server);
        });
        send_request_to(
            &path,
            &SummonRequest {
                action: SummonAction::Toggle,
                token: Some("act.token".into()),
                session_id: None,
            },
        )
        .expect("send");
        let got = rx.recv_timeout(Duration::from_secs(2)).expect("recv");
        assert_eq!(
            got,
            SummonRequest {
                action: SummonAction::Toggle,
                token: Some("act.token".into()),
                session_id: None,
            }
        );
        handle.join().unwrap();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn prepare_bind_path_keeps_live_socket() {
        use std::os::unix::net::UnixListener;

        let dir = std::env::temp_dir().join(format!("anqa-hud-summon-live-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("hud-summon.sock");
        let _ = std::fs::remove_file(&path);
        let _listener = UnixListener::bind(&path).expect("bind");
        let err = prepare_bind_path(&path).expect_err("live");
        assert!(path.exists(), "live inode must remain");
        assert!(matches!(err, SummonError::AlreadyRunning(_)));
        assert!(socket_accepts(&path));
        drop(_listener);
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn prepare_bind_path_removes_stale_inode() {
        let dir =
            std::env::temp_dir().join(format!("anqa-hud-summon-stale-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("hud-summon.sock");
        std::fs::write(&path, b"").expect("stale file");
        prepare_bind_path(&path).expect("stale");
        assert!(!path.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
