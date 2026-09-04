//! anqa-hud — iced desktop palette (control client).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    anqa_hud::log::install_panic_hook();
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.iter().any(|a| a == "--install-desktop") {
        let code = match anqa_hud::install_desktop::run_cli() {
            Ok(_) => 0,
            Err(err) => {
                eprintln!("anqa: {err}");
                1
            }
        };
        std::process::exit(code);
    }
    if args.iter().any(|a| a == "--version" || a == "-V") {
        println!("anqa {}", anqa_hud::VERSION);
        std::process::exit(0);
    }
    if args.iter().any(|a| a == "--help" || a == "-h") {
        eprintln!(
            "anqa {} — session palette (control client)\n\
             \n\
             Options:\n\
               --install-desktop   Write user-local icons and a launcher entry\n\
                                   (Linux .desktop, macOS ~/Applications/anqa.app,\n\
                                   Windows Start Menu shortcut). No system package.\n\
               --show              Show the palette. Starts anqa when nothing is running.\n\
               --hide              Hide the overlay (running anqa)\n\
               --toggle            Show or hide (running anqa; Sway bind target).
                                   Forwards XDG_ACTIVATION_TOKEN to the palette.\n\
               --open <session>    Show and open a catalog session (running anqa).\n\
               -V, --version       Print the product version\n\
               -h, --help          Show this help\n\
             \n\
             With no options, starts anqa (tray; X11 summon hotkey when available).\n\
             Wayland: use --show/--toggle, tray Show, or a compositor bind.",
            anqa_hud::VERSION
        );
        std::process::exit(0);
    }
    if let Some(sid) = cli_open_session(&args) {
        match anqa_hud::summon::send_request(anqa_hud::summon::SummonRequest::open(sid)) {
            Ok(()) => std::process::exit(0),
            Err(err) => {
                eprintln!("anqa: {err}");
                std::process::exit(1);
            }
        }
    }
    if let Some(action) = cli_summon_action(&args) {
        match anqa_hud::summon::plan_summon_cli(action, anqa_hud::summon::send_command(action)) {
            Ok(anqa_hud::summon::SummonCli::Done) => std::process::exit(0),
            Ok(anqa_hud::summon::SummonCli::StartShown) => {
                std::env::set_var(anqa_hud::tray::SHOW_ON_START_ENV, "1");
            }
            Err(err) => {
                eprintln!("anqa: {err}");
                std::process::exit(1);
            }
        }
    }
    #[cfg(target_os = "macos")]
    anqa_hud::macoswin::prepare_host();
    let code = match anqa_hud::run() {
        Ok(()) => 0,
        Err(err) => {
            eprintln!("anqa: {err}");
            1
        }
    };
    // Tray and notify threads outlive the iced loop.
    std::process::exit(code);
}

fn cli_summon_action(args: &[String]) -> Option<anqa_hud::summon::SummonAction> {
    for a in args {
        match a.as_str() {
            "--show" => return Some(anqa_hud::summon::SummonAction::Show),
            "--hide" => return Some(anqa_hud::summon::SummonAction::Hide),
            "--toggle" => return Some(anqa_hud::summon::SummonAction::Toggle),
            _ => {}
        }
    }
    None
}

fn cli_open_session(args: &[String]) -> Option<String> {
    let mut iter = args.iter();
    while let Some(a) = iter.next() {
        if a.as_str() == "--open" {
            return iter
                .next()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty());
        }
        if let Some(sid) = a.strip_prefix("--open=") {
            let sid = sid.trim();
            if !sid.is_empty() {
                return Some(sid.to_string());
            }
        }
    }
    None
}
