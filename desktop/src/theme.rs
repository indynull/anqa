//! Map Textual theme tokens (``config.toml`` ``theme``) onto iced.

use std::sync::OnceLock;

use iced::Color;
use iced::Theme;
use serde_json::Value;

use crate::format::BrandRole;

pub use icedtea::theme::{mix, relative_luma, Tokens};

use icedtea::density::DensityName;
use icedtea::m3::{ElevationPolicy, ShapePolicy};

/// Live look knobs (same set as the icedtea gallery strip).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Look {
    pub density: DensityName,
    pub font_scale: f32,
    pub shape: ShapePolicy,
    pub elevation: ElevationPolicy,
}

impl Default for Look {
    fn default() -> Self {
        Self {
            density: DensityName::Default,
            font_scale: 1.0,
            shape: ShapePolicy::Soft,
            elevation: ElevationPolicy::Desktop,
        }
    }
}

impl Look {
    pub fn density_label(self) -> &'static str {
        match self.density {
            DensityName::Compact => "Compact",
            DensityName::Default => "Default",
            DensityName::Comfortable => "Comfortable",
        }
    }

    pub fn scale_label(self) -> &'static str {
        match self.font_scale {
            x if (x - 0.875).abs() < 0.01 => "90%",
            x if (x - 1.125).abs() < 0.01 => "110%",
            x if (x - 1.25).abs() < 0.01 => "125%",
            _ => "100%",
        }
    }

    pub fn shape_label(self) -> &'static str {
        match self.shape {
            ShapePolicy::Tight => "Tight",
            ShapePolicy::Soft => "Soft",
            ShapePolicy::Pill => "Pill",
            ShapePolicy::Material => "Material",
            ShapePolicy::Desktop => "Desktop",
        }
    }

    pub fn elevation_label(self) -> &'static str {
        match self.elevation {
            ElevationPolicy::Flat => "Flat",
            ElevationPolicy::Desktop => "Desktop",
        }
    }

    pub fn with_density_label(mut self, name: &str) -> Self {
        self.density = match name {
            "Compact" => DensityName::Compact,
            "Comfortable" => DensityName::Comfortable,
            _ => DensityName::Default,
        };
        self
    }

    pub fn with_scale_label(mut self, name: &str) -> Self {
        self.font_scale = match name {
            "90%" => 0.875,
            "110%" => 1.125,
            "125%" => 1.25,
            _ => 1.0,
        };
        self
    }

    pub fn with_shape_label(mut self, name: &str) -> Self {
        self.shape = match name {
            "Tight" => ShapePolicy::Tight,
            "Soft" => ShapePolicy::Soft,
            "Pill" => ShapePolicy::Pill,
            "Material" => ShapePolicy::Material,
            _ => ShapePolicy::Desktop,
        };
        self
    }

    pub fn with_elevation_label(mut self, name: &str) -> Self {
        self.elevation = match name {
            "Flat" => ElevationPolicy::Flat,
            _ => ElevationPolicy::Desktop,
        };
        self
    }
}

/// Color for a timeline / status role from the active theme tokens.
pub fn brand_role_color(role: BrandRole, tok: Tokens) -> Color {
    match role {
        BrandRole::Cream => tok.text,
        BrandRole::Complete => tok.success,
        BrandRole::Running => tok.warning,
        BrandRole::Failed => tok.danger,
        BrandRole::Cancelled => tok.muted,
    }
}

/// Same mix as TUI ``TAG_WASH`` (primary / success / … onto surface).
pub const BADGE_WASH: f32 = 0.22;

/// Badge fill and readable ink for a role color.
pub fn badge_face(ink: Color, tok: Tokens) -> (Color, Color) {
    let wash = mix(ink, tok.surface, BADGE_WASH);
    (wash, ink_on(ink, wash))
}

const CATALOG: &str = include_str!("../assets/textual-themes.json");
const PAIRS: &str = include_str!("../assets/theme-pairs.json");

/// True when ``$surface`` is a dark canvas (gruvbox, nord, …).
pub fn canvas_is_dark(tok: Tokens) -> bool {
    relative_luma(tok.canvas) < 0.45
}

fn srgb_lin(c: f32) -> f32 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

fn wcag_luma(c: Color) -> f32 {
    0.2126 * srgb_lin(c.r) + 0.7152 * srgb_lin(c.g) + 0.0722 * srgb_lin(c.b)
}

/// Textual `Color.brightness` (Rec.601). `$text` is auto-ink on this.
fn rec601_brightness(c: Color) -> f32 {
    (299.0 * c.r + 587.0 * c.g + 114.0 * c.b) / 1000.0
}

/// Textual `$text` (`auto 87%`) / `$text-muted` (`auto 60%`) on *canvas*.
fn tui_auto_ink(canvas: Color, alpha: f32) -> Color {
    let ink = if rec601_brightness(canvas) < 0.5 {
        Color::WHITE
    } else {
        Color::BLACK
    };
    mix(ink, canvas, alpha)
}

/// WCAG relative-luminance contrast between two sRGB colors.
pub fn contrast_ratio(a: Color, b: Color) -> f32 {
    let (l1, l2) = (wcag_luma(a), wcag_luma(b));
    let (hi, lo) = if l1 > l2 { (l1, l2) } else { (l2, l1) };
    (hi + 0.05) / (lo + 0.05)
}

/// Mix ``ink`` toward black or white until it holds 4.5:1 on ``canvas``.
pub fn ink_on(ink: Color, canvas: Color) -> Color {
    ink_on_at(ink, canvas, 4.5)
}

/// Mix ``ink`` toward black or white until it holds ``min`` contrast on ``canvas``.
pub fn ink_on_at(ink: Color, canvas: Color, min: f32) -> Color {
    if contrast_ratio(ink, canvas) >= min {
        return ink;
    }
    let toward = if relative_luma(canvas) < 0.45 {
        Color::WHITE
    } else {
        Color::BLACK
    };
    let mut lo = 0.0f32;
    let mut hi = 1.0f32;
    let mut best = toward;
    for _ in 0..12 {
        let mid = (lo + hi) * 0.5;
        let candidate = mix(toward, ink, mid);
        if contrast_ratio(candidate, canvas) >= min {
            best = candidate;
            hi = mid;
        } else {
            lo = mid;
        }
    }
    best
}

fn parse_hex(s: &str) -> Option<Color> {
    let t = s.trim().trim_start_matches('#');
    if t.len() < 6 || !t.as_bytes()[..6].iter().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let r = u8::from_str_radix(&t[0..2], 16).ok()?;
    let g = u8::from_str_radix(&t[2..4], 16).ok()?;
    let b = u8::from_str_radix(&t[4..6], 16).ok()?;
    Some(Color::from_rgb8(r, g, b))
}

fn color_of(colors: &Value, key: &str, fallback: Color) -> Color {
    colors
        .get(key)
        .and_then(Value::as_str)
        .and_then(parse_hex)
        .unwrap_or(fallback)
}

fn user_theme_tokens(name: &str) -> Option<Tokens> {
    let dir = crate::prefs::themes_dir()?;
    let path = dir.join(format!("{name}.toml"));
    let text = std::fs::read_to_string(path).ok()?;
    let rec: toml::Value = text.parse().ok()?;
    let table = rec.as_table()?;
    let hex = |keys: &[&str], fallback: Color| {
        keys.iter()
            .find_map(|k| table.get(*k).and_then(|v| v.as_str()).and_then(parse_hex))
            .unwrap_or(fallback)
    };
    let canvas = hex(&["background", "canvas"], Color::from_rgb8(18, 18, 20));
    let text = tui_auto_ink(canvas, 0.87);
    let primary = hex(&["primary"], Color::from_rgb8(1, 120, 212));
    Some(Tokens::from_aliases(
        canvas,
        hex(&["surface"], mix(text, canvas, 0.08)),
        hex(&["panel"], mix(text, canvas, 0.10)),
        text,
        tui_auto_ink(canvas, 0.60),
        primary,
        hex(&["accent"], Color::from_rgb8(254, 166, 43)),
        hex(&["success"], Color::from_rgb8(78, 191, 113)),
        hex(&["warning"], Color::from_rgb8(254, 166, 43)),
        hex(&["error", "danger"], Color::from_rgb8(185, 60, 91)),
        hex(&["primary-background"], mix(primary, canvas, 0.35)),
    ))
}

fn catalog_colors(name: &str) -> Option<Value> {
    let root = serde_json::from_str::<Value>(CATALOG).ok()?;
    let key = name.trim();
    if key.is_empty() {
        return None;
    }
    root.get(key)?.get("colors").cloned()
}

fn is_auto(pref: &str) -> bool {
    matches!(
        pref.trim().to_ascii_lowercase().as_str(),
        "" | "auto" | "system" | "default" | "anqa" | "anqa-light"
    )
}

fn host_pair(appearance: icedtea::theme::Appearance) -> String {
    match appearance {
        icedtea::theme::Appearance::Light => "light".into(),
        icedtea::theme::Appearance::Dark => "dark".into(),
    }
}

/// Portal `color-scheme`: 1 dark; 2, 0, or empty is light (same as the TUI).
pub fn appearance_from_portal_stdout(out: &str) -> icedtea::theme::Appearance {
    if out.contains("uint32 1") {
        icedtea::theme::Appearance::Dark
    } else {
        icedtea::theme::Appearance::Light
    }
}

/// `None` (no preference) uses the portal, then light.
pub fn appearance_from_mode(
    mode: icedtea::iced::theme::Mode,
    _current: icedtea::theme::Appearance,
) -> icedtea::theme::Appearance {
    match mode {
        icedtea::iced::theme::Mode::Dark => icedtea::theme::Appearance::Dark,
        icedtea::iced::theme::Mode::Light => icedtea::theme::Appearance::Light,
        icedtea::iced::theme::Mode::None => {
            portal_appearance().unwrap_or(icedtea::theme::Appearance::Light)
        }
    }
}

/// Session portal color-scheme, when this host publishes one.
pub fn portal_appearance() -> Option<icedtea::theme::Appearance> {
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let out = std::process::Command::new("gdbus")
                .args([
                    "call",
                    "--session",
                    "--dest=org.freedesktop.portal.Desktop",
                    "--object-path=/org/freedesktop/portal/desktop",
                    "--method=org.freedesktop.portal.Settings.Read",
                    "org.freedesktop.appearance",
                    "color-scheme",
                ])
                .output();
            let _ = tx.send(out);
        });
        match rx.recv_timeout(std::time::Duration::from_secs(1)) {
            Ok(Ok(out)) if out.status.success() => Some(appearance_from_portal_stdout(
                &String::from_utf8_lossy(&out.stdout),
            )),
            Ok(Ok(_)) | Ok(Err(_)) | Err(_) => Some(icedtea::theme::Appearance::Light),
        }
    }
    #[cfg(not(all(unix, not(target_os = "macos"))))]
    {
        None
    }
}

/// Textual ANSI pair members → icedtea host pair (no hex catalog for live ANSI).
fn ansi_member(pref: &str) -> Option<&'static str> {
    match pref.trim().to_ascii_lowercase().as_str() {
        "ansi-light" => Some("light"),
        "ansi-dark" => Some("dark"),
        _ => None,
    }
}

fn family_pairs() -> &'static std::collections::BTreeMap<String, (String, String)> {
    static MAP: OnceLock<std::collections::BTreeMap<String, (String, String)>> = OnceLock::new();
    MAP.get_or_init(|| {
        let mut out = std::collections::BTreeMap::new();
        let Ok(root) = serde_json::from_str::<Value>(PAIRS) else {
            return out;
        };
        let Some(obj) = root.as_object() else {
            return out;
        };
        for (id, val) in obj {
            let Some(arr) = val.as_array() else {
                continue;
            };
            if arr.len() != 2 {
                continue;
            }
            let (Some(light), Some(dark)) = (arr[0].as_str(), arr[1].as_str()) else {
                continue;
            };
            out.insert(id.clone(), (light.to_string(), dark.to_string()));
        }
        out
    })
}

fn pair_members(pref: &str) -> Option<(String, String)> {
    let key = pref.trim();
    let map = family_pairs();
    if let Some(pair) = map.get(key) {
        return Some(pair.clone());
    }
    map.values()
        .find(|(light, dark)| light == key || dark == key)
        .cloned()
}

fn hud_face(member: &str) -> String {
    match member {
        "ansi-light" => "light".into(),
        "ansi-dark" => "dark".into(),
        other => other.to_string(),
    }
}

/// Config ``theme``. ``auto`` is the host pair. ``follow`` flips named pairs.
pub fn resolve_name(pref: &str, appearance: icedtea::theme::Appearance, follow: bool) -> String {
    if is_auto(pref) {
        return host_pair(appearance);
    }
    if let Some(mapped) = ansi_member(pref) {
        if follow {
            return host_pair(appearance);
        }
        return mapped.into();
    }
    if !follow {
        return pref.to_string();
    }
    let Some((light, dark)) = pair_members(pref) else {
        return pref.to_string();
    };
    let member = match appearance {
        icedtea::theme::Appearance::Light => light,
        icedtea::theme::Appearance::Dark => dark,
    };
    hud_face(&member)
}

/// Tokens for ``theme`` in ``~/.anqa/config.toml``.
///
/// Default density is pad and control height. Type scale is 1.0
/// (Material body). Shape is Soft; elevation is Desktop.
pub fn tokens(name: &str) -> Tokens {
    tokens_with(name, Look::default())
}

/// Theme colors with live look knobs (density, type scale, shape, elevation).
pub fn tokens_with(name: &str, look: Look) -> Tokens {
    let key = name.trim();
    let tok = if let Some(user) = user_theme_tokens(key) {
        user
    } else if catalog_colors(key).is_some() {
        textual_tokens(key)
    } else {
        icedtea::theme::named(key).tokens
    };
    tok.with_density(icedtea::m3::Density::named(look.density))
        .with_font_scale(look.font_scale)
        .with_shape(look.shape)
        .with_elevation(look.elevation)
}

fn textual_tokens(name: &str) -> Tokens {
    // Same roles the terminal paints: `$text` / `$text-muted` are
    // ColorSystem ``auto 87%`` / ``auto 60%`` on the paper, not the
    // raw ``foreground`` / ``foreground-darken-2`` hex dump.
    let colors = catalog_colors(name).unwrap_or(Value::Null);
    let fallback_bg = Color::from_rgb8(18, 18, 20);
    let canvas = color_of(&colors, "background", fallback_bg);
    let text = tui_auto_ink(canvas, 0.87);
    let muted = tui_auto_ink(canvas, 0.60);
    let primary = color_of(&colors, "primary", Color::from_rgb8(1, 120, 212));
    let accent = color_of(&colors, "accent", Color::from_rgb8(254, 166, 43));
    let success = color_of(&colors, "success", Color::from_rgb8(78, 191, 113));
    let warning = color_of(&colors, "warning", Color::from_rgb8(254, 166, 43));
    let danger = color_of(&colors, "error", Color::from_rgb8(185, 60, 91));
    let surface = color_of(&colors, "surface", mix(text, canvas, 0.08));
    let panel = color_of(&colors, "panel", mix(text, canvas, 0.10));
    let border = color_of(&colors, "primary-background", mix(primary, canvas, 0.35));
    Tokens::from_aliases(
        canvas, surface, panel, text, muted, primary, accent, success, warning, danger, border,
    )
}

/// Textual theme names registered on icedtea's catalog.
pub fn catalog() -> &'static icedtea::theme::ThemeCatalog {
    static CATALOG_MAP: OnceLock<icedtea::theme::ThemeCatalog> = OnceLock::new();
    CATALOG_MAP.get_or_init(|| {
        let mut cat = icedtea::theme::ThemeCatalog::new();
        let Ok(root) = serde_json::from_str::<Value>(CATALOG) else {
            return cat;
        };
        let Some(obj) = root.as_object() else {
            return cat;
        };
        for key in obj.keys() {
            let tok = tokens(key);
            cat.register(key.clone(), tok, canvas_is_dark(tok));
        }
        cat
    })
}

pub fn iced_theme(name: &str) -> Theme {
    icedtea::theme::iced_theme(name, tokens(name))
}

/// Tokens for a config preference. ``auto`` layers host paper when *chrome*
/// has any fields (macOS / Windows). Empty chrome on Linux is a no-op.
pub fn paint_tokens(
    pref: &str,
    appearance: icedtea::theme::Appearance,
    follow_pairs: bool,
    look: Look,
    chrome: icedtea::theme::OsChrome,
) -> Tokens {
    let name = resolve_name(pref, appearance, follow_pairs);
    let tok = tokens_with(&name, look);
    if is_auto(pref) {
        icedtea::theme::apply_os_chrome(tok, true, chrome)
    } else {
        tok
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn named_theme_uses_textual_background_and_status() {
        let t = tokens("textual-dark");
        assert_eq!(t.canvas, Color::from_rgb8(0x12, 0x12, 0x12));
        assert_eq!(t.surface, Color::from_rgb8(0x1E, 0x1E, 0x1E));
        assert_eq!(t.primary, Color::from_rgb8(0x01, 0x78, 0xD4));
        assert_eq!(t.success, Color::from_rgb8(0x4E, 0xBF, 0x71));
        assert_eq!(t.warning, Color::from_rgb8(0xFE, 0xA6, 0x2B));
        assert_eq!(t.danger, Color::from_rgb8(0xB9, 0x3C, 0x5B));
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection_text, t.text);
    }

    #[test]
    fn gruvbox_is_in_catalog() {
        let t = tokens("gruvbox");
        assert_ne!(t.canvas, tokens("textual-dark").canvas);
        assert_ne!(t.canvas, tokens("nord").canvas);
        assert!(canvas_is_dark(t));
        assert!(canvas_is_dark(tokens("textual-dark")));
        assert!(!canvas_is_dark(tokens("solarized-light")));
        assert!(catalog().get("gruvbox").is_some());
        assert!(catalog().get("textual-dark").is_some());
    }

    #[test]
    fn flexoki_matches_tui_background() {
        let t = tokens("flexoki");
        assert_eq!(t.canvas, Color::from_rgb8(0x10, 0x0F, 0x0F));
        assert_eq!(t.surface, Color::from_rgb8(0x1C, 0x1B, 0x1A));
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection_text, t.text);
        assert_eq!(t.panel, Color::from_rgb8(0x28, 0x27, 0x26));
        assert_eq!(t.success, Color::from_rgb8(0x65, 0x80, 0x0B));
        assert_eq!(t.warning, Color::from_rgb8(0xAC, 0x83, 0x01));
        assert_eq!(t.danger, Color::from_rgb8(0xAE, 0x30, 0x29));
    }

    #[test]
    fn solarized_light_selected_row_keeps_readable_ink() {
        let t = tokens("solarized-light");
        assert_ne!(t.selection, t.accent);
        assert_eq!(t.selection_text, t.text);
        assert!((relative_luma(t.selection) - relative_luma(t.text)).abs() > 0.20);
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
    }

    #[test]
    fn catalog_hex_drops_textual_alpha_suffix() {
        let t = tokens("gruvbox");
        assert_eq!(t.text.a, 1.0);
        assert_eq!(t.muted.a, 1.0);
        assert_eq!(t.accent, Color::from_rgb8(0xF9, 0xBD, 0x2F));
        assert_eq!(t.warning, Color::from_rgb8(0xFD, 0x80, 0x19));
        assert_eq!(t.danger, Color::from_rgb8(0xFA, 0x49, 0x34));
        assert_eq!(t.success, Color::from_rgb8(0xB7, 0xBB, 0x26));
        assert_eq!(t.canvas, Color::from_rgb8(0x28, 0x28, 0x28));
    }

    #[test]
    fn auto_applies_os_paper_named_theme_does_not() {
        use iced::Color;
        use icedtea::theme::{apply_os_chrome, Appearance, OsChrome};
        let paper = Color::from_rgb8(0xE8, 0xE6, 0xE1);
        let ink = Color::from_rgb8(0x1D, 0x1D, 0x1F);
        let chrome = OsChrome {
            canvas: Some(paper),
            text: Some(ink),
            ..OsChrome::empty()
        };
        let auto = paint_tokens("auto", Appearance::Light, false, Look::default(), chrome);
        assert_eq!(auto.canvas, paper);
        assert_eq!(auto.text, ink);
        let empty = paint_tokens(
            "auto",
            Appearance::Light,
            false,
            Look::default(),
            OsChrome::empty(),
        );
        assert_eq!(empty.canvas, tokens("light").canvas);
        let nord = paint_tokens("nord", Appearance::Light, false, Look::default(), chrome);
        assert_ne!(nord.canvas, paper);
        assert_eq!(nord.canvas, tokens("nord").canvas);
        let _ = apply_os_chrome;
    }

    #[test]
    fn textual_and_nightfox_follow_like_tui() {
        use icedtea::theme::Appearance;
        assert_eq!(
            resolve_name("textual", Appearance::Light, true),
            "textual-light"
        );
        assert_eq!(
            resolve_name("textual-dark", Appearance::Light, true),
            "textual-light"
        );
        assert_eq!(resolve_name("nightfox", Appearance::Light, true), "dawnfox");
        assert_eq!(resolve_name("dawnfox", Appearance::Dark, true), "nightfox");
    }

    #[test]
    fn portal_stdout_maps_color_scheme() {
        use icedtea::theme::Appearance;
        assert_eq!(
            appearance_from_portal_stdout("(<<uint32 1>>,)"),
            Appearance::Dark
        );
        assert_eq!(
            appearance_from_portal_stdout("(<<uint32 2>>,)"),
            Appearance::Light
        );
        assert_eq!(
            appearance_from_portal_stdout("(<<uint32 0>>,)"),
            Appearance::Light
        );
        assert_eq!(appearance_from_portal_stdout(""), Appearance::Light);
    }

    #[test]
    fn ansi_pref_is_a_family_not_auto() {
        use icedtea::theme::Appearance;
        assert_eq!(resolve_name("ansi", Appearance::Light, true), "light");
        assert_eq!(resolve_name("ansi", Appearance::Dark, false), "ansi");
        assert_eq!(resolve_name("ansi-light", Appearance::Dark, false), "light");
    }

    #[test]
    fn mode_none_keeps_current_when_portal_is_silent() {
        use icedtea::iced::theme::Mode;
        use icedtea::theme::Appearance;
        assert_eq!(
            appearance_from_mode(Mode::Dark, Appearance::Light),
            Appearance::Dark
        );
        assert_eq!(
            appearance_from_mode(Mode::Light, Appearance::Dark),
            Appearance::Light
        );
    }

    #[test]
    fn auto_follows_host_pair() {
        use icedtea::theme::Appearance;
        assert_eq!(resolve_name("auto", Appearance::Light, false), "light");
        assert_eq!(resolve_name("", Appearance::Dark, false), "dark");
        assert_eq!(resolve_name("system", Appearance::Light, true), "light");
        assert_eq!(resolve_name("default", Appearance::Dark, false), "dark");
        assert_eq!(resolve_name("anqa", Appearance::Light, false), "light");
    }

    #[test]
    fn ansi_pair_pins_unless_follow() {
        use icedtea::theme::Appearance;
        assert_eq!(resolve_name("ansi-light", Appearance::Dark, false), "light");
        assert_eq!(resolve_name("ansi-dark", Appearance::Light, false), "dark");
        assert_eq!(resolve_name("ansi-light", Appearance::Dark, true), "dark");
        assert_eq!(
            resolve_name("tokyo-night", Appearance::Light, false),
            "tokyo-night"
        );
        assert_eq!(
            resolve_name("tokyo-night", Appearance::Light, true),
            "tokyo-night-day"
        );
        let light = paint_tokens(
            "ansi-light",
            Appearance::Dark,
            false,
            Look::default(),
            icedtea::theme::OsChrome::empty(),
        );
        assert_eq!(light.canvas, Color::from_rgb8(0xF3, 0xF3, 0xF3));
    }

    #[test]
    fn gruvbox_follows_desktop_to_icedtea_light_pair() {
        use icedtea::theme::Appearance;
        assert_eq!(
            resolve_name("gruvbox", Appearance::Light, true),
            "gruvbox-light"
        );
        assert_eq!(resolve_name("gruvbox", Appearance::Dark, true), "gruvbox");
        assert_eq!(
            resolve_name("gruvbox-light", Appearance::Dark, false),
            "gruvbox-light"
        );
        assert!(!canvas_is_dark(tokens("gruvbox-light")));
        assert_eq!(
            tokens("gruvbox").text,
            tui_auto_ink(tokens("gruvbox").canvas, 0.87)
        );
    }

    #[test]
    fn mix_is_opaque_between_endpoints() {
        let a = Color::from_rgb8(255, 0, 0);
        let b = Color::from_rgb8(0, 0, 0);
        let m = mix(a, b, 0.5);
        assert!((m.r - 0.5).abs() < 0.01);
        assert_eq!(m.a, 1.0);
    }

    #[test]
    fn brand_role_colors_follow_theme_tokens() {
        use crate::format::BrandRole;
        let tok = tokens("textual-dark");
        assert_eq!(brand_role_color(BrandRole::Cream, tok), tok.text);
        assert_eq!(brand_role_color(BrandRole::Complete, tok), tok.success);
        assert_eq!(brand_role_color(BrandRole::Running, tok), tok.warning);
        assert_eq!(brand_role_color(BrandRole::Failed, tok), tok.danger);
        assert_eq!(brand_role_color(BrandRole::Cancelled, tok), tok.muted);
    }

    #[test]
    fn badge_face_washes_role_ink_onto_surface() {
        use crate::format::BrandRole;
        let tok = tokens("textual-dark");
        assert_eq!(BADGE_WASH, 0.22);
        let (wash, fg) = badge_face(tok.success, tok);
        assert_eq!(wash, mix(tok.success, tok.surface, BADGE_WASH));
        assert_eq!(fg, ink_on(tok.success, wash));
        let (cream_wash, _) = badge_face(brand_role_color(BrandRole::Cream, tok), tok);
        assert_eq!(cream_wash, mix(tok.text, tok.surface, BADGE_WASH));
        assert_ne!(cream_wash, mix(tok.primary, tok.surface, BADGE_WASH));
        let (tag_wash, _) = badge_face(tok.primary, tok);
        assert_eq!(tag_wash, mix(tok.primary, tok.surface, BADGE_WASH));
    }

    #[test]
    fn named_theme_tokens_are_icedtea_tokens() {
        let t = tokens("textual-dark");
        assert_eq!(t.selection, icedtea::theme::mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.surface, Color::from_rgb8(0x1E, 0x1E, 0x1E));
        let registered = catalog().resolve("textual-dark");
        assert_eq!(registered.selection, t.selection);
        assert_eq!(registered.primary, t.primary);
        let light = tokens("solarized-light");
        assert_ne!(light.canvas, t.canvas);
        assert_eq!(
            light.selection,
            icedtea::theme::mix(light.primary, light.canvas, 0.28)
        );
    }

    #[test]
    fn textual_catalog_scheme_matches_short_fields() {
        for name in ["textual-dark", "gruvbox", "solarized-light", "flexoki"] {
            let t = tokens(name);
            let s = t.scheme();
            assert_eq!(s.surface, t.canvas, "{name} canvas");
            assert_eq!(s.surface_container, t.surface, "{name} surface");
            assert_eq!(s.surface_container_high, t.panel, "{name} panel");
            assert_eq!(s.on_surface, t.text, "{name} text");
            assert_eq!(s.on_surface_variant, t.muted, "{name} muted");
            assert_eq!(s.primary, t.primary, "{name} primary");
            assert_eq!(s.secondary, t.accent, "{name} accent");
            assert_eq!(s.success, t.success, "{name} success");
            assert_eq!(s.warning, t.warning, "{name} warning");
            assert_eq!(s.error, t.danger, "{name} danger");
            assert_eq!(s.outline, t.border, "{name} border");
            assert_eq!(s.secondary_container, t.selection, "{name} selection");
            assert_eq!(
                s.on_secondary_container, t.selection_text,
                "{name} sel text"
            );
        }
    }

    #[test]
    fn catalog_body_and_mute_follow_textual_auto_ink() {
        for name in [
            "solarized-dark",
            "solarized-light",
            "nord",
            "gruvbox",
            "tokyo-night",
            "catppuccin-mocha",
            "textual-dark",
        ] {
            let t = tokens(name);
            let body = tui_auto_ink(t.canvas, 0.87);
            let mute = tui_auto_ink(t.canvas, 0.60);
            assert_eq!(t.text, body, "{name} $text");
            assert_eq!(t.muted, mute, "{name} $text-muted");
            assert_ne!(t.text, t.muted, "{name}");
        }
        let raw_fg = Color::from_rgb8(0x83, 0x94, 0x96);
        let solar = tokens("solarized-dark");
        assert_ne!(solar.text, raw_fg);
        assert!(contrast_ratio(solar.text, solar.canvas) > contrast_ratio(raw_fg, solar.canvas));
    }

    #[test]
    fn ink_on_lifts_olive_off_cream_and_keeps_it_on_ink() {
        let cream = Color::from_rgb8(0xFB, 0xF1, 0xC7);
        let ink = Color::from_rgb8(0x28, 0x28, 0x28);
        let olive = Color::from_rgb8(0x98, 0x97, 0x1A);
        let gold = Color::from_rgb8(0xD7, 0x99, 0x21);
        assert!(contrast_ratio(olive, cream) < 4.5);
        assert!(contrast_ratio(ink_on(olive, cream), cream) >= 4.5);
        assert!(contrast_ratio(ink_on(gold, cream), cream) >= 4.5);
        assert_eq!(ink_on(olive, ink), olive);
        assert!(contrast_ratio(ink_on(olive, ink), ink) >= 4.5);
    }

    #[test]
    fn hud_tokens_use_default_density_and_type_steps_match_roles() {
        let t = tokens("textual-dark");
        assert_eq!(t.density.name, icedtea::m3::DensityName::Default);
        assert!((t.font_scale - 1.0).abs() < f32::EPSILON);
        let scale = t.font_scale;
        let step = |role: icedtea::typo::TypeRole| (role.size() as f32 * scale).round();
        assert_eq!(t.meta(), step(icedtea::typo::TypeRole::Meta));
        assert_eq!(t.body(), step(icedtea::typo::TypeRole::Body));
        assert_eq!(t.title(), step(icedtea::typo::TypeRole::Title));
        assert_eq!(t.code(), step(icedtea::typo::TypeRole::Code));
        assert_eq!(crate::live::diff_hunk_line_h(), t.code() * 1.3);
    }

    #[test]
    fn painted_faces_use_token_type_steps() {
        let view = include_str!("view.rs");
        let kit = include_str!("kit.rs");
        let app = include_str!("app.rs");
        let prod_view = view.split("#[cfg(test)]").next().expect("view");
        let prod_kit = kit.split("#[cfg(test)]").next().expect("kit");
        let prod_app = app.split("#[cfg(test)]").next().expect("app");
        for src in [prod_view, prod_kit] {
            assert!(
                !src.contains("typo::META")
                    && !src.contains("typo::BODY")
                    && !src.contains("typo::TITLE")
                    && !src.contains("typo::CODE"),
                "paint sizes must be Tokens type steps"
            );
        }
        assert!(prod_view.contains(".size(tea.meta())") || prod_view.contains(".size(tok.meta())"));
        assert!(prod_view.contains(".size(tea.body())"));
        assert!(prod_view.contains(".size(tok.title())"));
        assert!(prod_app.contains("tokens(\"textual-dark\").body()"));
    }

    #[test]
    fn look_knobs_match_gallery_steps() {
        let d = Look::default();
        assert_eq!(d.density_label(), "Default");
        assert_eq!(d.scale_label(), "100%");
        assert_eq!(d.shape_label(), "Soft");
        assert_eq!(d.elevation_label(), "Desktop");
        assert_eq!(
            tokens_with("textual-dark", d.with_density_label("Comfortable"))
                .density
                .name,
            DensityName::Comfortable
        );
        let scaled = tokens_with("textual-dark", d.with_scale_label("100%"));
        assert!((scaled.font_scale - 1.0).abs() < f32::EPSILON);
        assert_eq!(
            tokens_with("textual-dark", d.with_shape_label("Pill")).shape,
            ShapePolicy::Pill
        );
        assert_eq!(
            tokens_with("textual-dark", d.with_elevation_label("Flat")).elevation,
            ElevationPolicy::Flat
        );
    }
}
