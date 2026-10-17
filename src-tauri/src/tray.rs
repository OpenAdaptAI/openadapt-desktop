// Minimal in-window system tray for OpenAdapt Desktop.
//
// NOTE ON SCOPE: the always-on, cross-platform status tray (recording/sync/break
// badge, quick actions, cloud count poll) is a SEPARATE application — the
// `openadapt-tray` repo (pystray, owned by workstream W3). It talks to this
// desktop app over a localhost loopback socket (spec §3d).
//
// This module therefore keeps only a lightweight Tauri tray so the desktop app
// itself is reachable from the menu bar (show window / quit). The rich status
// model lives in the standalone tray, not here.
//
// The recording indicator remains a compliance requirement wherever a tray is
// shown: the tooltip reflects whether recording is active.
//
// Shadow mode (off by default) has its own indicator here, because a person
// must be able to see at a glance that it is on: a menu line that reads
// "Shadow mode: on" or "Shadow mode: off", the tooltip, and on macOS a short
// title next to the icon. The window sets it through `set_shadow_tray_status`
// from the engine's shadow state. The menu line is not clickable: turning
// shadow mode on needs the notice in the window, and "Shadow mode settings"
// opens that window.

use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    App, AppHandle, Manager, Wry,
};

const TRAY_ID: &str = "openadapt-desktop-tray";
const TOOLTIP: &str = "OpenAdapt Desktop";

/// The tray's shadow-mode menu line, kept so its text can change.
pub struct ShadowTrayItem(pub Mutex<Option<MenuItem<Wry>>>);

/// Menu text, tooltip, and macOS title for one shadow-mode state.
pub fn shadow_labels(active: bool) -> (&'static str, &'static str, Option<&'static str>) {
    if active {
        (
            "Shadow mode: on (local only)",
            "OpenAdapt Desktop: shadow mode is on",
            Some("Shadow"),
        )
    } else {
        ("Shadow mode: off", TOOLTIP, None)
    }
}

/// Build the minimal tray icon + context menu.
pub fn setup_tray(app: &App) -> Result<(), Box<dyn std::error::Error>> {
    let show = MenuItem::with_id(app, "show", "Open OpenAdapt", true, None::<&str>)?;
    let (label, _, _) = shadow_labels(false);
    let shadow = MenuItem::with_id(app, "shadow-status", label, false, None::<&str>)?;
    let shadow_settings = MenuItem::with_id(
        app,
        "shadow-settings",
        "Shadow mode settings…",
        true,
        None::<&str>,
    )?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &shadow, &shadow_settings, &quit])?;
    app.manage(ShadowTrayItem(Mutex::new(Some(shadow))));

    TrayIconBuilder::with_id(TRAY_ID)
        .tooltip(TOOLTIP)
        .icon(
            app.default_window_icon()
                .cloned()
                .ok_or("missing default window icon")?,
        )
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => show_main(app),
            "shadow-settings" => {
                show_main(app);
                use tauri::Emitter;
                let _ = app.emit("tray://open-shadow-settings", ());
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .build(app)?;

    Ok(())
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Reflect shadow mode's on/off state in the tray. Called by the window
/// whenever the engine reports a shadow state.
#[tauri::command]
pub fn set_shadow_tray_status(app: AppHandle, active: bool) -> Result<(), String> {
    let (label, tooltip, title) = shadow_labels(active);
    if let Some(state) = app.try_state::<ShadowTrayItem>() {
        if let Some(item) = state.0.lock().map_err(|e| e.to_string())?.as_ref() {
            item.set_text(label).map_err(|e| e.to_string())?;
        }
    }
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        tray.set_tooltip(Some(tooltip)).map_err(|e| e.to_string())?;
        #[cfg(target_os = "macos")]
        tray.set_title(title).map_err(|e| e.to_string())?;
        #[cfg(not(target_os = "macos"))]
        let _ = title;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::shadow_labels;

    #[test]
    fn shadow_indicator_says_on_or_off() {
        let (on, on_tip, on_title) = shadow_labels(true);
        assert!(on.contains("on") && on_tip.contains("shadow mode is on"));
        assert_eq!(on_title, Some("Shadow"));
        let (off, _, off_title) = shadow_labels(false);
        assert!(off.ends_with("off"));
        assert_eq!(off_title, None);
    }
}
