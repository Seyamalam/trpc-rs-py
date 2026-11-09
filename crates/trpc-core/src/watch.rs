//! Filesystem watching for `trpc-py dev` live type sync.
//!
//! A [`DirectoryWatcher`] wraps `notify`'s recommended backend behind a
//! channel and yields **debounced batches** of changed paths: one save burst
//! (an editor write plus metadata touches) arrives as a single batch so the
//! caller regenerates once per logical change, not once per OS event.
//!
//! Only `Create`/`Modify`/`Remove` events are reported; `Access` and
//! metadata-only noise are ignored. Filtering to Python sources is left to
//! the caller — the watcher is deliberately file-agnostic.

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::mpsc::{Receiver, channel};
use std::time::{Duration, Instant};

use notify::{Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher, recommended_watcher};

use crate::error::{ErrorCode, TrpcError};

/// Hard cap on how long a burst of filesystem events is coalesced; keeps a
/// continuously-written tree from starving regeneration forever.
const MAX_DEBOUNCE_WINDOW: Duration = Duration::from_secs(2);

/// Recursive filesystem watcher yielding debounced batches of changed paths.
///
/// Dropping the watcher stops the backend thread. `next_batch` blocks the
/// calling thread; callers holding the Python GIL must release it first (the
/// `trpc-py` binding does).
pub struct DirectoryWatcher {
    // The backend watches only while this field is alive.
    _watcher: RecommendedWatcher,
    rx: Receiver<notify::Result<Event>>,
}

/// The event kinds that mean "file contents or membership changed".
fn is_relevant(kind: EventKind) -> bool {
    matches!(
        kind,
        EventKind::Create(_) | EventKind::Modify(_) | EventKind::Remove(_)
    )
}

impl DirectoryWatcher {
    /// Watch every path in `paths` recursively.
    ///
    /// # Errors
    /// Returns `INTERNAL_SERVER_ERROR` when the backend cannot start or a
    /// path cannot be watched (missing directory, permission denied).
    pub fn new(paths: &[PathBuf]) -> Result<Self, TrpcError> {
        let (tx, rx) = channel();
        let mut watcher = recommended_watcher(move |event| {
            // A dead receiver only means the watcher was dropped mid-send.
            let _ = tx.send(event);
        })
        .map_err(|error| {
            TrpcError::new(
                ErrorCode::InternalServerError,
                format!("failed to start the file watcher: {error}"),
            )
        })?;
        for path in paths {
            watcher
                .watch(path, RecursiveMode::Recursive)
                .map_err(|error| {
                    TrpcError::new(
                        ErrorCode::InternalServerError,
                        format!("cannot watch {}: {error}", path.display()),
                    )
                })?;
        }
        Ok(Self {
            _watcher: watcher,
            rx,
        })
    }

    /// Block up to `wait` for the next debounced batch of changed paths.
    ///
    /// After the first event arrives, further events are coalesced until the
    /// channel stays quiet for `debounce` or [`MAX_DEBOUNCE_WINDOW`] elapses.
    /// Returns `None` on timeout, `Some(Err(_))` when the backend reports a
    /// watcher error, and `Some(Ok(paths))` with deduplicated sorted paths.
    ///
    /// # Errors
    /// Returns `INTERNAL_SERVER_ERROR` when the OS backend reports an event
    /// error (for example a watched directory was removed).
    pub fn next_batch(
        &self,
        wait: Duration,
        debounce: Duration,
    ) -> Result<Option<Vec<PathBuf>>, TrpcError> {
        let first = match self.rx.recv_timeout(wait) {
            Ok(first) => first,
            Err(_timeout) => return Ok(None),
        };
        let mut paths = BTreeSet::new();
        collect(first, &mut paths)?;

        // Coalesce the burst: keep draining while events arrive within the
        // debounce window, capped so a busy tree cannot starve the caller.
        let deadline = Instant::now() + MAX_DEBOUNCE_WINDOW;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break;
            }
            match self.rx.recv_timeout(remaining.min(debounce)) {
                Ok(event) => collect(event, &mut paths)?,
                Err(_quiet) => break,
            }
        }
        Ok(Some(paths.into_iter().collect()))
    }
}

/// Fold one backend event into the changed-path set.
fn collect(event: notify::Result<Event>, paths: &mut BTreeSet<PathBuf>) -> Result<(), TrpcError> {
    match event {
        Ok(event) => {
            if is_relevant(event.kind) {
                paths.extend(event.paths);
            }
            Ok(())
        }
        Err(error) => Err(TrpcError::new(
            ErrorCode::InternalServerError,
            format!("file watcher error: {error}"),
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn watcher_for(dir: &std::path::Path) -> DirectoryWatcher {
        DirectoryWatcher::new(&[dir.to_path_buf()]).expect("watcher starts")
    }

    #[test]
    fn create_and_modify_events_arrive_as_one_batch() {
        let dir = std::env::temp_dir().join(format!("trpc-watch-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let watcher = watcher_for(&dir);

        // Several quick writes coalesce into a single batch.
        let file = dir.join("a.py");
        fs::write(&file, b"x = 1").unwrap();
        fs::write(&file, b"x = 2").unwrap();
        let batch = watcher
            .next_batch(Duration::from_secs(5), Duration::from_millis(200))
            .unwrap()
            .expect("batch arrives");
        assert!(batch.iter().any(|path| path.ends_with("a.py")));

        // A subsequent write produces another batch.
        fs::write(dir.join("b.py"), b"y = 3").unwrap();
        let batch = watcher
            .next_batch(Duration::from_secs(5), Duration::from_millis(200))
            .unwrap()
            .expect("batch arrives");
        assert!(batch.iter().any(|path| path.ends_with("b.py")));
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn timeout_returns_none() {
        let dir = std::env::temp_dir().join(format!("trpc-watch-idle-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let watcher = watcher_for(&dir);
        assert!(
            watcher
                .next_batch(Duration::from_millis(50), Duration::from_millis(10))
                .unwrap()
                .is_none()
        );
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn watching_a_missing_directory_fails() {
        let missing = std::env::temp_dir().join(format!("trpc-watch-gone-{}", std::process::id()));
        let error = DirectoryWatcher::new(&[missing]).err().unwrap();
        assert_eq!(error.code_name, "INTERNAL_SERVER_ERROR");
    }
}
