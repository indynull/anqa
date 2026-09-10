//! Escape pops the top live layer. Surfaces register here; they do not bind Esc.

/// A dismissible HUD layer, front-most first.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layer {
    /// Session tag picker.
    Tags,
    /// `?` shortcut cheatsheet.
    Help,
    /// Right-click menu.
    Context,
    /// Timeline event body or workflow inspect.
    Timeline,
    /// Notes compose form.
    Compose,
    /// Child session; Esc returns to the parent.
    Parent,
    /// Hide the palette overlay.
    Palette,
}

impl Layer {
    /// Front-most to back-most. Escape pops the first live entry.
    pub const ORDER: &[Self] = &[
        Self::Tags,
        Self::Help,
        Self::Context,
        Self::Timeline,
        Self::Compose,
        Self::Parent,
        Self::Palette,
    ];

    /// Transient overlay: Esc closes it even when a field captured the key.
    pub fn is_overlay(self) -> bool {
        matches!(self, Self::Tags | Self::Help | Self::Context)
    }

    /// First live layer in [`Layer::ORDER`].
    pub fn top(mut live: impl FnMut(Self) -> bool) -> Option<Self> {
        Self::ORDER.iter().copied().find(|layer| live(*layer))
    }
}

#[cfg(test)]
mod tests {
    use super::Layer;

    #[test]
    fn top_is_the_front_most_live_layer() {
        assert_eq!(
            Layer::top(|layer| matches!(layer, Layer::Help | Layer::Timeline | Layer::Palette)),
            Some(Layer::Help)
        );
        assert_eq!(
            Layer::top(|layer| matches!(layer, Layer::Timeline | Layer::Palette)),
            Some(Layer::Timeline)
        );
        assert_eq!(
            Layer::top(|layer| layer == Layer::Palette),
            Some(Layer::Palette)
        );
        assert_eq!(Layer::top(|_| false), None);
    }

    #[test]
    fn overlays_are_the_transient_sheets() {
        assert!(Layer::Help.is_overlay());
        assert!(Layer::Tags.is_overlay());
        assert!(Layer::Context.is_overlay());
        assert!(!Layer::Timeline.is_overlay());
        assert!(!Layer::Compose.is_overlay());
        assert!(!Layer::Parent.is_overlay());
        assert!(!Layer::Palette.is_overlay());
    }
}
