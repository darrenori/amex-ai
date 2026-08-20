// Application bootstrap: login, view switching, and the two rendered views.

import './styles/tokens.css';
import './styles/base.css';
import './styles/app.css';

import { api } from './api.js';
import { escapeHtml } from './format.js';
import { closeModal } from './components/modal.js';
import { renderAccount } from './views/account.js';
import { renderRecovery } from './views/recovery.js';

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const dom = {
  loginView: document.getElementById('view-login'),
  appView: document.getElementById('view-app'),
  form: document.getElementById('loginForm'),
  email: document.getElementById('email'),
  password: document.getElementById('password'),
  emailError: document.getElementById('emailError'),
  passwordError: document.getElementById('passwordError'),
  formStatus: document.getElementById('formStatus'),
  loginButton: document.getElementById('loginButton'),
  loginButtonText: document.getElementById('loginButtonText'),
  spinner: document.querySelector('#loginButton .spinner'),
  togglePassword: document.getElementById('togglePassword'),
  restartButton: document.getElementById('restartButton'),
  memberChip: document.getElementById('memberChip'),
  main: document.getElementById('main'),
  accountView: document.getElementById('accountView'),
  recoveryView: document.getElementById('recoveryView'),
  liveStatus: document.getElementById('liveStatus'),
};

const session = {
  account: null,
  profiles: [],
  stopRecovery: null,
};

const announce = (message) => {
  dom.liveStatus.textContent = '';
  window.setTimeout(() => {
    dom.liveStatus.textContent = message;
  }, 10);
};

function showFieldError(input, node, message) {
  input.setAttribute('aria-invalid', String(Boolean(message)));
  node.textContent = message ?? '';
}

function showBanner(container, message) {
  container.innerHTML = `
    <div class="alert" role="alert">
      <span class="icon-badge critical" aria-hidden="true">!</span>
      <div>
        <strong>Something went wrong</strong>
        <p>${escapeHtml(message)}</p>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

dom.togglePassword.addEventListener('click', () => {
  const showing = dom.password.type === 'text';
  dom.password.type = showing ? 'password' : 'text';
  dom.togglePassword.textContent = showing ? 'Show' : 'Hide';
  dom.togglePassword.setAttribute('aria-pressed', String(!showing));
  dom.password.focus();
});

dom.email.addEventListener('input', () => {
  if (dom.email.getAttribute('aria-invalid') === 'true') showFieldError(dom.email, dom.emailError, '');
});

dom.password.addEventListener('input', () => {
  if (dom.password.getAttribute('aria-invalid') === 'true') showFieldError(dom.password, dom.passwordError, '');
});

document.getElementById('forgotLink').addEventListener('click', (event) => {
  event.preventDefault();
  announce('Password recovery is available in the full experience.');
});

document.getElementById('createAccount').addEventListener('click', () => {
  announce('Account creation is available in the full experience.');
});

function validateLocally() {
  const email = dom.email.value.trim();
  let firstInvalid = null;

  if (!email) {
    showFieldError(dom.email, dom.emailError, 'Enter your email or User ID.');
    firstInvalid = dom.email;
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    showFieldError(dom.email, dom.emailError, 'Enter a valid email address.');
    firstInvalid = dom.email;
  } else {
    showFieldError(dom.email, dom.emailError, '');
  }

  if (!dom.password.value) {
    showFieldError(dom.password, dom.passwordError, 'Enter your password.');
    firstInvalid = firstInvalid ?? dom.password;
  } else {
    showFieldError(dom.password, dom.passwordError, '');
  }

  if (firstInvalid) {
    dom.formStatus.textContent = 'Please correct the highlighted fields.';
    firstInvalid.focus();
    return false;
  }
  return true;
}

function setLoading(loading) {
  dom.loginButton.disabled = loading;
  dom.loginButton.classList.toggle('is-loading', loading);
  dom.spinner.hidden = !loading;
  dom.loginButtonText.textContent = loading ? 'Preparing your journey…' : 'Log in';
}

dom.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!validateLocally()) return;

  setLoading(true);
  dom.formStatus.textContent = 'Checking your details.';

  try {
    const [, account, profiles] = await Promise.all([
      api.login(dom.email.value.trim(), dom.password.value),
      api.account(),
      api.profiles(),
    ]);

    session.account = account;
    session.profiles = profiles.profiles;

    // A short, composed pause — the brand does not rush a sign-in.
    await new Promise((resolve) => window.setTimeout(resolve, reducedMotion.matches ? 120 : 700));
    enterApp();
  } catch (error) {
    showFieldError(dom.password, dom.passwordError, error.message);
    dom.formStatus.textContent = error.message;
    dom.password.focus();
    dom.password.select();
  } finally {
    setLoading(false);
  }
});

// ---------------------------------------------------------------------------
// Views
// ---------------------------------------------------------------------------

function showAccount() {
  if (session.stopRecovery) {
    session.stopRecovery();
    session.stopRecovery = null;
  }
  dom.recoveryView.hidden = true;
  dom.accountView.hidden = false;
  window.scrollTo({ top: 0, behavior: reducedMotion.matches ? 'auto' : 'smooth' });
  announce('Returned to the account overview.');
}

async function showRecovery() {
  try {
    const payload = await api.simulate(session.profiles[0]?.id ?? 'time');
    dom.accountView.hidden = true;
    dom.recoveryView.hidden = false;
    session.stopRecovery = renderRecovery(dom.recoveryView, {
      payload,
      profiles: session.profiles,
      onBack: showAccount,
      announce,
    });
    window.scrollTo({ top: 0, behavior: reducedMotion.matches ? 'auto' : 'smooth' });
    announce('Flight SQ638 has been cancelled. Three recovery paths are ready to review.');
  } catch (error) {
    dom.accountView.hidden = true;
    dom.recoveryView.hidden = false;
    showBanner(dom.recoveryView, error.message);
  }
}

function enterApp() {
  dom.loginView.classList.remove('is-active');
  dom.appView.classList.add('is-active');
  dom.appView.setAttribute('aria-hidden', 'false');
  dom.memberChip.hidden = false;
  dom.memberChip.textContent = `${session.account.member.name} · ${session.account.member.tier}`;

  renderAccount(dom.accountView, {
    data: session.account,
    onSimulate: showRecovery,
    announce,
  });

  dom.accountView.hidden = false;
  dom.recoveryView.hidden = true;
  window.scrollTo({ top: 0, behavior: 'auto' });
  window.setTimeout(() => dom.main.focus(), reducedMotion.matches ? 20 : 240);
  announce(`You are now signed in. Welcome back, ${session.account.member.first_name}.`);
}

dom.restartButton.addEventListener('click', () => {
  if (session.stopRecovery) {
    session.stopRecovery();
    session.stopRecovery = null;
  }
  closeModal(false);
  dom.appView.classList.remove('is-active');
  dom.appView.setAttribute('aria-hidden', 'true');
  dom.loginView.classList.add('is-active');
  dom.memberChip.hidden = true;
  dom.accountView.innerHTML = '';
  dom.recoveryView.innerHTML = '';
  dom.form.reset();
  dom.password.type = 'password';
  dom.togglePassword.textContent = 'Show';
  dom.togglePassword.setAttribute('aria-pressed', 'false');
  showFieldError(dom.email, dom.emailError, '');
  showFieldError(dom.password, dom.passwordError, '');
  dom.formStatus.textContent = '';
  window.scrollTo({ top: 0, behavior: 'auto' });
  window.setTimeout(() => dom.email.focus(), reducedMotion.matches ? 20 : 240);
});

document.getElementById('homeLink').addEventListener('click', (event) => {
  event.preventDefault();
  showAccount();
});
