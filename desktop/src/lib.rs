//! anqa-hud library: control decode, domain helpers, and the iced palette.

/// Product version (same string as the Python package).
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub mod app;
pub mod brand;
pub mod control;
pub mod desktop;
pub mod diff_tree;
pub mod dismiss;
pub mod format;
pub mod fuzzy;
pub mod help;
pub mod install_desktop;
pub mod keys;
pub mod kit;
pub mod live;
pub mod log;
pub mod macoswin;
pub mod model;
pub mod motion;
pub mod place;
#[cfg(target_os = "linux")]
pub mod place_linux;
pub mod prefs;
pub mod query;
pub mod shortcut;
pub mod summon;
pub mod theme;
pub mod tray;
pub mod typo;
pub mod view;
pub mod wire;

#[cfg(target_os = "linux")]
pub mod wlactivate;
#[cfg(target_os = "linux")]
pub mod x11focus;

pub use app::run;
