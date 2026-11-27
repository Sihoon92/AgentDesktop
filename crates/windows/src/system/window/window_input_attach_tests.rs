use super::should_attach;

#[test]
fn no_foreground_window_is_never_attached() {
    assert!(!should_attach(7, 0, true));
}

#[test]
fn the_callers_own_thread_is_never_attached() {
    assert!(!should_attach(7, 7, true));
}

#[test]
fn a_hung_foreground_thread_is_never_attached() {
    assert!(!should_attach(7, 9, false));
}

#[test]
fn a_responsive_foreign_foreground_thread_is_attached() {
    assert!(should_attach(7, 9, true));
}
