/**
 * SweetAlert2 — themed utility wrappers for the app.
 *
 * Usage:
 *   import { Toast, Confirm, Alert } from '../src/utils/swal';
 *
 *   Toast.success('Device added');
 *   Toast.error('Something broke', 'Check your network');
 *   const ok = await Confirm.danger('Delete device R1?', 'This cannot be undone.');
 *   Alert.info('Heads up', 'The sync will run in the background.');
 */
import Swal from 'sweetalert2';

// ---------------------------------------------------------------------------
//  Utility: force SweetAlert2 to finish cleanup even if animations stall.
//  SweetAlert2 sets overflow:hidden on <body> when a modal opens and removes
//  it via animationend. If Tailwind's animate classes race with SweetAlert2's
//  own classes the body override can get stuck — causing the "sticks until
//  resize" repaint bug.  We call this in every didClose / willClose.
// ---------------------------------------------------------------------------
function forceSwalCleanup() {
  try {
    document.body.style.overflow = '';
    document.body.style.paddingRight = '';
    document.body.classList.remove('swal2-shown', 'swal2-height-auto', 'swal2-iosfix');
    // Remove any lingering SweetAlert2 backdrop elements
    document.querySelectorAll('.swal2-container').forEach((el) => {
      (el as HTMLElement).style.display = 'none';
    });
    // Allow a microtask tick then fully remove them so React re-renders cleanly
    setTimeout(() => {
      document.querySelectorAll('.swal2-container').forEach((el) => el.remove());
    }, 0);
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
//  Base mixin — used only for centered dialogs (Confirm / Alert)
//
//  NOTE: We intentionally do NOT set custom showClass/hideClass here.
//  SweetAlert2's built-in swal2-show / swal2-hide CSS animations are what
//  it tracks via `animationend` to remove the backdrop.  Replacing them with
//  Tailwind animate-out classes breaks that listener, causing the modal to
//  stay visible until the browser repaints (e.g. window resize).
// ---------------------------------------------------------------------------
const baseMixin = Swal.mixin({
  customClass: {
    popup: '!bg-base-100 !text-base-content !rounded-xl !shadow-2xl',
    title: '!text-base-content',
    htmlContainer: '!text-base-content/80',
    confirmButton: 'btn btn-primary btn-sm',
    cancelButton: 'btn btn-ghost btn-sm',
    denyButton: 'btn btn-error btn-sm',
  },
  buttonsStyling: false,
  timerProgressBar: true,
  didClose: forceSwalCleanup,
});

// ---------------------------------------------------------------------------
//  Toast — compact auto-dismissing corner notification
//
//  Deliberately does NOT inherit baseMixin so that the SweetAlert2 internal
//  toast layout is untouched.  We only override colors via CSS vars so the
//  compact sizing and auto-dismiss timer both work correctly.
// ---------------------------------------------------------------------------
const ToastBase = Swal.mixin({
  toast: true,
  position: 'top-end',
  showConfirmButton: false,
  timer: 4000,
  timerProgressBar: true,
  // Minimal class overrides — keep the popup small
  customClass: {
    popup: '!rounded-lg !text-sm !shadow-lg !border !border-base-300 !bg-base-200',
    title: '!text-base-content !text-sm !font-semibold !m-0 !p-0',
    htmlContainer: '!text-base-content/70 !text-xs !m-0 !p-0 !mt-0.5',
    timerProgressBar: '!bg-primary',
  },
  buttonsStyling: false,
  // Ensure the popup width stays compact
  width: 'auto',
  padding: '0.6rem 1rem',
  showClass: { popup: 'animate-in slide-in-from-right-4 fade-in duration-200' },
  hideClass: { popup: 'animate-out slide-out-to-right-4 fade-out duration-150' },
  didOpen: (toast) => {
    toast.addEventListener('mouseenter', Swal.stopTimer);
    toast.addEventListener('mouseleave', Swal.resumeTimer);
  },
});

export const Toast = {
  success: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'success', title, text }),

  error: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'error', title, text }),

  warning: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'warning', title, text }),

  info: (title: string, text?: string) =>
    ToastBase.fire({ icon: 'info', title, text }),

  /**
   * Show a centered modal dialog that auto-dismisses, then optionally
   * redirects the browser to `redirectUrl` (string) after it closes.
   * Useful for important notifications that need user attention.
   */
  modalRedirect: (
    title: string,
    text?: string,
    redirectUrl?: string,
    timer: number = 3500,
    icon: 'success' | 'error' | 'info' | 'warning' | 'question' = 'info',
  ) => {
    return baseMixin
      .fire({
        icon,
        title,
        text,
        showConfirmButton: false,
        timer,
        timerProgressBar: true,
        allowOutsideClick: true,
        didOpen: (popup) => {
          popup.addEventListener('mouseenter', Swal.stopTimer);
          popup.addEventListener('mouseleave', Swal.resumeTimer);
        },
        // didClose is inherited from baseMixin (forceSwalCleanup)
      })
      .then(() => {
        if (redirectUrl) {
          setTimeout(() => {
            try { window.location.assign(redirectUrl); } catch { /* ignore */ }
          }, 150);
        }
      });
  },
};

// ---------------------------------------------------------------------------
//  Confirm — centered dialog that returns a boolean
// ---------------------------------------------------------------------------
export const Confirm = {
  /** Red "danger" confirm (e.g. delete). Returns true if confirmed. */
  danger: async (title: string, text?: string): Promise<boolean> => {
    try {
      const result = await baseMixin.fire({
        icon: 'warning',
        title,
        text,
        showCancelButton: true,
        confirmButtonText: 'Yes, proceed',
        cancelButtonText: 'Cancel',
        customClass: {
          popup: '!bg-base-100 !text-base-content !rounded-xl !shadow-2xl',
          title: '!text-base-content',
          htmlContainer: '!text-base-content/80',
          confirmButton: 'btn btn-error btn-sm',
          cancelButton: 'btn btn-ghost btn-sm',
        },
        reverseButtons: true,
      });
      return result.isConfirmed;
    } finally {
      try { Swal.close(); } catch { /* ignore */ }
    }
  },

  /** Neutral confirm (e.g. rollback). Returns true if confirmed. */
  ask: async (title: string, text?: string): Promise<boolean> => {
    try {
      const result = await baseMixin.fire({
        icon: 'question',
        title,
        text,
        showCancelButton: true,
        confirmButtonText: 'Confirm',
        cancelButtonText: 'Cancel',
        reverseButtons: true,
      });
      return result.isConfirmed;
    } finally {
      try { Swal.close(); } catch { /* ignore */ }
    }
  },
};

// ---------------------------------------------------------------------------
//  LoginToast — welcome banner with countdown bar + slide-right dismiss
//
//  NOTE: `fill-mode-forwards` was intentionally removed from hideClass.
//  That property freezes the final CSS keyframe on the element, which means
//  SweetAlert2's cleanup (removing the container, restoring body overflow)
//  never fires — producing the "sticks until resize" bug.
// ---------------------------------------------------------------------------
const LoginToastBase = Swal.mixin({
  toast: true,
  position: 'top-end',
  showConfirmButton: false,
  timer: 3000,
  timerProgressBar: true,
  customClass: {
    popup: '!bg-base-100 !text-base-content !rounded-xl !shadow-2xl !border !border-success/30',
    title: '!text-base-content !font-bold',
    htmlContainer: '!text-base-content/70',
    timerProgressBar: '!bg-success',
  },
  buttonsStyling: false,
  showClass: { popup: 'animate-in slide-in-from-right duration-300' },
  hideClass: { popup: 'animate-out slide-out-to-right duration-200' },
  didOpen: (toast) => {
    toast.addEventListener('mouseenter', Swal.stopTimer);
    toast.addEventListener('mouseleave', Swal.resumeTimer);
  },
  didClose: forceSwalCleanup,
});

export const LoginToast = {
  welcome: (username: string) =>
    LoginToastBase.fire({
      icon: 'success',
      title: `Welcome back, ${username}`,
      text: 'Redirecting to dashboard…',
    }),
};

// ---------------------------------------------------------------------------
//  Alert — centered informational dialog (single OK button)
// ---------------------------------------------------------------------------
export const Alert = {
  success: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'success', title, text }),

  error: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'error', title, text }),

  warning: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'warning', title, text }),

  info: (title: string, text?: string) =>
    baseMixin.fire({ icon: 'info', title, text }),
};
