//! Borrowing the foreground thread's input state for one raise.
//!
//! Windows grants `SetForegroundWindow` only to the process that owns the
//! foreground or received the last input event. A CLI started behind a
//! browser or a terminal owns neither, so its raise is refused and the
//! taskbar button flashes instead. Attaching this thread's input queue to
//! the foreground window's thread for the duration of the raise makes the
//! request come from the foreground's input state, which Windows accepts.
//!
//! A thread that is not dispatching messages is never attached: attached
//! threads share input processing, so attaching to a hung foreground thread
//! could stall this process with it. The attachment is released when the
//! returned [`InputAttachment`] drops, on every exit path.

#[cfg(target_os = "windows")]
use super::window_enum::window_is_responsive;

/// Whether this thread may attach to the foreground thread: there is a
/// foreground thread, it is not this thread, and it is dispatching messages.
pub(crate) fn should_attach(
    current_tid: u32,
    foreground_tid: u32,
    foreground_responsive: bool,
) -> bool {
    foreground_tid != 0 && foreground_tid != current_tid && foreground_responsive
}

/// An input-queue attachment from this thread to the foreground thread,
/// detached on drop.
pub(crate) struct InputAttachment {
    current_tid: u32,
    foreground_tid: u32,
}

impl Drop for InputAttachment {
    fn drop(&mut self) {
        detach(self.current_tid, self.foreground_tid);
    }
}

#[cfg(target_os = "windows")]
fn detach(current_tid: u32, foreground_tid: u32) {
    use windows_sys::Win32::Foundation::FALSE;
    use windows_sys::Win32::System::Threading::AttachThreadInput;

    unsafe {
        let _ = AttachThreadInput(current_tid, foreground_tid, FALSE);
    }
}

#[cfg(not(target_os = "windows"))]
fn detach(_current_tid: u32, _foreground_tid: u32) {}

/// Attaches this thread's input queue to the current foreground window's
/// thread when [`should_attach`] allows it and Windows accepts the attach.
#[cfg(target_os = "windows")]
pub(crate) fn attach_to_foreground_input() -> Option<InputAttachment> {
    use windows_sys::Win32::Foundation::{FALSE, TRUE};
    use windows_sys::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowThreadProcessId,
    };

    let foreground = unsafe { GetForegroundWindow() };
    if foreground.is_null() {
        return None;
    }
    let foreground_tid = unsafe { GetWindowThreadProcessId(foreground, std::ptr::null_mut()) };
    let current_tid = unsafe { GetCurrentThreadId() };
    let responsive = foreground_tid != 0 && window_is_responsive(foreground);
    if !should_attach(current_tid, foreground_tid, responsive) {
        return None;
    }
    if unsafe { AttachThreadInput(current_tid, foreground_tid, TRUE) } == FALSE {
        return None;
    }
    Some(InputAttachment {
        current_tid,
        foreground_tid,
    })
}

#[cfg(not(target_os = "windows"))]
pub(crate) fn attach_to_foreground_input() -> Option<InputAttachment> {
    None
}

#[cfg(test)]
#[path = "window_input_attach_tests.rs"]
mod tests;
