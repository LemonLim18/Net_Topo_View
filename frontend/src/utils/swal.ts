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
//  Base mixin — used only for centered dialogs (Confirm / Alert)
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
  showClass: { popup: 'animate-in fade-in zoom-in-95 duration-200' },
  hideClass: { popup: 'animate-out fade-out zoom-out-95 duration-150' },
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
};

// ---------------------------------------------------------------------------
//  Confirm — centered dialog that returns a boolean
// ---------------------------------------------------------------------------
export const Confirm = {
  /** Red "danger" confirm (e.g. delete). Returns true if confirmed. */
  danger: async (title: string, text?: string): Promise<boolean> => {
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
  },

  /** Neutral confirm (e.g. rollback). Returns true if confirmed. */
  ask: async (title: string, text?: string): Promise<boolean> => {
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
  },
};

// ---------------------------------------------------------------------------
//  LoginToast — welcome banner with countdown bar + slide-right dismiss
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
  hideClass: { popup: 'animate-out slide-out-to-right duration-400 fill-mode-forwards' },
  didOpen: (toast) => {
    toast.addEventListener('mouseenter', Swal.stopTimer);
    toast.addEventListener('mouseleave', Swal.resumeTimer);
  },
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
