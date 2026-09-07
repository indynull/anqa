//! HUD motion: icedtea jobs, interruptible page/overlay clocks, chrome runs.

use std::time::{Duration, Instant};

use iced::Animation;
use icedtea::motion::{AttentionFace, Axis, Enter, Job, Run, SwitchFace};

use crate::model::Tab;

/// Palette present (hotkey show).
pub const PRESENT_MS: u64 = 220;
/// Palette dismiss (Esc hide). Shorter than present.
pub const DISMISS_MS: u64 = 180;
/// Hierarchical enter (session pick, first event).
pub const PUSH_MS: u64 = 240;
/// Hierarchical leave (back to session list).
pub const POP_MS: u64 = 200;
/// Incoming event-body fade while chrome stays.
pub const EVENT_FADE_MS: u64 = 120;
/// Search-hint appear / empty-state fade.
pub const HINT_MS: u64 = 100;

/// What the operator is doing. Page and palette clocks keep iced
/// [`Animation`] so a mid-flight retune keeps progress. Chrome jobs
/// ([`Run`]) start from rest.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MotionRole {
    Present,
    Dismiss,
    Sibling,
    Push,
    Pop,
    Disclose,
    None,
}

/// Which body layer a page job paints.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PageLayer {
    /// Picker ↔ browse (search and footer stay).
    Browse,
    /// Tab / event body (tabs stay).
    Pane,
}

impl MotionRole {
    /// Full-motion length, or zero when reduced motion is on.
    pub fn duration(self, reduced: bool) -> Duration {
        if reduced || matches!(self, Self::None) {
            return Duration::ZERO;
        }
        Duration::from_millis(match self {
            Self::Present => PRESENT_MS,
            Self::Dismiss => DISMISS_MS,
            Self::Sibling => self.job().duration(true, false).as_millis() as u64,
            Self::Push => PUSH_MS,
            Self::Pop => POP_MS,
            Self::Disclose => icedtea::m3::motion::EXPAND.millis(),
            Self::None => 0,
        })
    }

    /// icedtea job this role drives.
    pub fn job(self) -> Job {
        match self {
            Self::Present | Self::Dismiss | Self::Push | Self::Pop => Job::Enter(Enter::Sheet),
            Self::Sibling => Job::Switch(SwitchFace::FadeThrough),
            Self::Disclose => Job::Disclose(Axis::Block),
            Self::None => Job::Value,
        }
    }

    /// Enter jobs decelerate; dismiss accelerates; pane fade uses the switch job.
    pub fn ease(self) -> icedtea::m3::Ease {
        match self {
            Self::Dismiss => icedtea::m3::Ease::EmphasizedAccelerate,
            Self::Sibling => self.job().ease(true),
            _ => icedtea::m3::Ease::EmphasizedDecelerate,
        }
    }

    /// iced [`Animation`] easing for this role.
    pub fn easing(self) -> iced::animation::Easing {
        self.ease().lilt()
    }
}

/// Slide to paint, if any. Sibling and reduced motion never translate.
pub fn visual_slide(
    role: MotionRole,
    slide: icedtea::motion::Slide,
    reduced: bool,
) -> icedtea::motion::Slide {
    if reduced || matches!(role, MotionRole::Sibling | MotionRole::None) {
        icedtea::motion::Slide::None
    } else {
        slide
    }
}

/// True when a new page job should start from 0 instead of keeping progress.
pub fn page_restarts(progress: f32, animating: bool) -> bool {
    !animating || progress >= 0.999
}

/// Tab change is a sibling fade.
pub fn tab_role(from: Tab, to: Tab) -> MotionRole {
    if from == to {
        MotionRole::None
    } else {
        MotionRole::Sibling
    }
}

/// First open is a hierarchical push. Stepping another event is not a page job.
pub fn event_open_role(already_open: bool) -> MotionRole {
    if already_open {
        MotionRole::None
    } else {
        MotionRole::Push
    }
}

/// Leave full-pane event detail.
pub fn event_close_role() -> MotionRole {
    MotionRole::Pop
}

/// Pick a session into browse.
pub fn session_enter_role() -> MotionRole {
    MotionRole::Push
}

/// Leave browse for the session list.
pub fn session_leave_role() -> MotionRole {
    MotionRole::Pop
}

/// Animation parked at `open` with this role's duration and ease.
pub fn role_animation(role: MotionRole, open: bool, reduced: bool) -> Animation<bool> {
    Animation::new(open)
        .duration(role.duration(reduced))
        .easing(role.easing())
}

/// Note expander height.
pub fn disclose_animation(open: bool, reduced: bool) -> Animation<bool> {
    icedtea::motion::expand_animation(open, reduced)
}

/// Palette / help / menu / pane / disclose / fade / shake clocks.
pub fn palette_job() -> Job {
    Job::Enter(Enter::Sheet)
}

pub fn help_job() -> Job {
    Job::Enter(Enter::Dialog)
}

pub fn menu_job() -> Job {
    Job::Enter(Enter::Menu)
}

pub fn pane_job() -> Job {
    Job::Switch(SwitchFace::FadeThrough)
}

pub fn disclose_job() -> Job {
    Job::Disclose(Axis::Block)
}

pub fn event_fade_job() -> Job {
    Job::Value
}

pub fn hint_job() -> Job {
    Job::Value
}

pub fn shake_job() -> Job {
    Job::Attention(AttentionFace::Shake)
}

/// Resting chrome run. Enter jobs hold the house present length.
pub fn run_job(job: Job, open: bool, reduced: bool) -> Run {
    let run = icedtea::motion::run(job, open, reduced);
    match job {
        Job::Enter(Enter::Sheet | Enter::Dialog) => run.lasting(Duration::from_millis(PRESENT_MS)),
        Job::Value => run.lasting(Duration::from_millis(HINT_MS)),
        _ => run,
    }
}

/// Incoming event-body fade. Always starts from 0 so a rapid step replaces.
pub fn start_event_fade(reduced: bool, now: Instant) -> Run {
    let mut run = rest_event_fade(reduced);
    if reduced {
        return run;
    }
    run = icedtea::motion::run(event_fade_job(), false, false)
        .lasting(Duration::from_millis(EVENT_FADE_MS));
    run.go(true, now);
    run
}

/// Event body at rest (fully visible).
pub fn rest_event_fade(reduced: bool) -> Run {
    icedtea::motion::run(event_fade_job(), true, reduced)
        .lasting(Duration::from_millis(EVENT_FADE_MS))
}

/// FadeThrough incoming opacity (blank, then fade in).
pub fn fade_through_in(progress: f32) -> f32 {
    SwitchFace::FadeThrough.incoming_fade(progress)
}

/// Drive a chrome run toward open or closed.
pub fn go_run(run: &mut Run, open: bool, now: Instant) {
    run.go(open, now);
}

/// Continue an in-flight page fade or start a new 0→1 job.
pub fn continue_or_restart(
    page: Animation<bool>,
    role: MotionRole,
    current: f32,
    animating: bool,
    reduced: bool,
    now: Instant,
) -> Animation<bool> {
    if reduced {
        return role_animation(role, true, true);
    }
    if page_restarts(current, animating) {
        let mut next = role_animation(role, false, false);
        next.go_mut(true, now);
        next
    } else {
        let mut next = page.duration(role.duration(false)).easing(role.easing());
        if !next.is_animating(now) {
            next.go_mut(true, now);
        }
        next
    }
}

/// Retune show/hide from the current overlay value (interruptible).
pub fn retune_overlay(
    overlay: Animation<bool>,
    open: bool,
    reduced: bool,
    now: Instant,
) -> Animation<bool> {
    let role = if open {
        MotionRole::Present
    } else {
        MotionRole::Dismiss
    };
    if reduced {
        return role_animation(role, open, true);
    }
    let mut next = overlay.duration(role.duration(false)).easing(role.easing());
    next.go_mut(open, now);
    next
}

/// Env / GTK animation off. Tests set [`crate::app::Hud`] directly.
pub fn detect_reduced_motion() -> bool {
    match std::env::var("ANQA_HUD_REDUCED_MOTION") {
        Ok(v) => matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        Err(_) => std::env::var("GTK_ENABLE_ANIMATIONS").is_ok_and(|v| v == "0"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn present_is_short_ease_out_dismiss_is_shorter_ease_in() {
        assert_eq!(PRESENT_MS, 220);
        assert_eq!(DISMISS_MS, 180);
        const { assert!(DISMISS_MS < PRESENT_MS) };
        assert_eq!(
            MotionRole::Present.ease(),
            icedtea::m3::Ease::EmphasizedDecelerate
        );
        assert_eq!(
            MotionRole::Dismiss.ease(),
            icedtea::m3::Ease::EmphasizedAccelerate
        );
        assert_eq!(
            MotionRole::Present.duration(false),
            Duration::from_millis(PRESENT_MS)
        );
        assert_eq!(
            MotionRole::Dismiss.duration(false),
            Duration::from_millis(DISMISS_MS)
        );
        assert_eq!(MotionRole::Present.duration(true), Duration::ZERO);
        assert_eq!(MotionRole::Dismiss.duration(true), Duration::ZERO);
    }

    #[test]
    fn tab_change_is_fade_not_slide() {
        assert_eq!(tab_role(Tab::Turns, Tab::Timeline), MotionRole::Sibling);
        assert_eq!(
            visual_slide(MotionRole::Sibling, icedtea::motion::Slide::End, false),
            icedtea::motion::Slide::None
        );
        assert_eq!(tab_role(Tab::Turns, Tab::Turns), MotionRole::None);
    }

    #[test]
    fn event_open_is_push_and_step_is_not_a_page_job() {
        assert_eq!(event_open_role(false), MotionRole::Push);
        assert_eq!(event_open_role(true), MotionRole::None);
        assert_eq!(event_close_role(), MotionRole::Pop);
        assert_eq!(
            visual_slide(MotionRole::Push, icedtea::motion::Slide::End, false),
            icedtea::motion::Slide::End
        );
        assert_eq!(
            visual_slide(MotionRole::Pop, icedtea::motion::Slide::Start, false),
            icedtea::motion::Slide::Start
        );
        assert_eq!(
            visual_slide(MotionRole::Push, icedtea::motion::Slide::End, true),
            icedtea::motion::Slide::None
        );
        assert_eq!(
            tab_role(Tab::Turns, Tab::Timeline).job(),
            Job::Switch(SwitchFace::FadeThrough)
        );
        assert_eq!(help_job(), Job::Enter(Enter::Dialog));
        assert_eq!(menu_job(), Job::Enter(Enter::Menu));
        assert_eq!(disclose_job(), Job::Disclose(Axis::Block));
        assert_eq!(shake_job(), Job::Attention(AttentionFace::Shake));
    }

    #[test]
    fn event_fade_restarts_from_zero() {
        let started = Instant::now() - Duration::from_millis(80);
        let mid = start_event_fade(false, started);
        let now = Instant::now();
        let p = mid.progress(now);
        assert!(p > 0.1 && p < 1.0, "mid-step fade {p}");
        let next = start_event_fade(false, now);
        assert!(
            next.progress(now) < 0.15,
            "rapid step must restart, got {}",
            next.progress(now)
        );
        let snap = start_event_fade(true, now);
        assert!((snap.progress(now) - 1.0).abs() < 0.01);
    }

    #[test]
    fn session_pick_is_push_and_home_is_pop() {
        assert_eq!(session_enter_role(), MotionRole::Push);
        assert_eq!(session_leave_role(), MotionRole::Pop);
    }

    #[test]
    fn mid_flight_page_does_not_restart_from_zero() {
        assert!(page_restarts(1.0, false));
        assert!(page_restarts(0.0, false));
        assert!(!page_restarts(0.4, true));
        let started = Instant::now() - Duration::from_millis(80);
        let mut page = role_animation(MotionRole::Sibling, false, false);
        page.go_mut(true, started);
        let now = Instant::now();
        let mid = page.interpolate(0.0, 1.0, now);
        assert!(
            mid > 0.1 && mid < 0.95,
            "expected mid-flight progress, got {mid}"
        );
        let next = continue_or_restart(page, MotionRole::Sibling, mid, true, false, now);
        let after = next.interpolate(0.0, 1.0, now);
        assert!(
            after > 0.1,
            "second job reset progress to {after} (was {mid})"
        );
    }

    #[test]
    fn reduced_motion_page_snaps_open() {
        let page = role_animation(MotionRole::Push, false, false);
        let next = continue_or_restart(page, MotionRole::Push, 0.0, false, true, Instant::now());
        assert!(!next.is_animating(Instant::now()));
        assert!((next.interpolate(0.0, 1.0, Instant::now()) - 1.0).abs() < 0.01);
    }

    #[test]
    fn overlay_present_and_dismiss_use_role_durations() {
        let now = Instant::now();
        let show = retune_overlay(
            role_animation(MotionRole::Present, false, false),
            true,
            false,
            now,
        );
        let hide = retune_overlay(
            role_animation(MotionRole::Present, true, false),
            false,
            false,
            now,
        );
        let show_left = show.remaining(now).as_millis() as u64;
        assert!(
            (200..=PRESENT_MS).contains(&show_left),
            "present remaining {show_left}"
        );
        assert!(hide.is_animating(now));
        assert!(
            hide.interpolate(0.0, 1.0, now) > 0.9,
            "dismiss starts from open"
        );
        let snapped = retune_overlay(show, false, true, now);
        assert!(!snapped.is_animating(now));
    }

    #[test]
    fn disclose_progress_is_between_ends_while_opening() {
        let started = Instant::now() - Duration::from_millis(80);
        let mut anim = disclose_animation(false, false);
        anim.go_mut(true, started);
        let p = anim.interpolate(0.0, 1.0, Instant::now());
        assert!(p > 0.0 && p < 1.0, "expander progress {p}");
        let mut snap = disclose_animation(false, true);
        snap.go_mut(true, Instant::now());
        assert!((snap.interpolate(0.0, 1.0, Instant::now()) - 1.0).abs() < 0.01);
    }
}
