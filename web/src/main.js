// Application bootstrap: view switching and the two top-level views, the calm
// account overview and the recovery console that takes over when a trip breaks.

import './styles/fonts.css';
import './styles/tokens.css';
import './styles/base.css';
import './styles/app.css';
import './styles/console.css';
import './styles/islands.css';
import './styles/home.css';

import { api } from './api.js';
import { maybeOpenTutorial, openTutorial } from './views/tutorial.js';
import { escapeHtml } from './format.js';
import { closeModal } from './components/modal.js';
import { renderAccount } from './views/account.js';
import { renderRecovery } from './views/recovery.js';
import { initHome } from './views/home.js';
import { unmountAll } from './islands/mount.js';

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

const dom = {
  homeView: document.getElementById('view-home'),
  appView: document.getElementById('view-app'),
  restartButton: document.getElementById('restartButton'),
  tourButton: document.getElementById('tourButton'),
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
// Home → app
// ---------------------------------------------------------------------------
//
// There is no sign-in step. The demo has one member and published credentials,
// so a login form asked people to type a password that was printed above the
// box it went into, and stood between them and the thing they came to see.
// The account is fetched on the way in instead.

let opening = false;

async function openApp(trigger = null) {
  if (opening) return;
  opening = true;
  const label = trigger?.textContent;
  if (trigger) {
    trigger.disabled = true;
    trigger.textContent = 'Opening…';
  }
  announce('Opening your account.');

  try {
    const [account, profiles] = await Promise.all([api.account(), api.profiles()]);
    session.account = account;
    session.profiles = profiles.profiles;
    enterApp();
  } catch (error) {
    // The backend sleeps on its free tier, so the first open of the day can
    // fail while it wakes. Say so plainly and leave the button usable.
    announce(error.message);
    if (trigger) {
      trigger.textContent = 'Try again';
      trigger.disabled = false;
    }
  } finally {
    opening = false;
    if (trigger && trigger.textContent === 'Opening…') {
      trigger.textContent = label;
      trigger.disabled = false;
    }
  }
}

function showHome() {
  dom.appView.classList.remove('is-active');
  dom.appView.setAttribute('aria-hidden', 'true');
  dom.homeView.classList.add('is-active');
  window.scrollTo({ top: 0, behavior: 'auto' });
}

initHome(dom.homeView, { onEnter: openApp, reducedMotion: reducedMotion.matches });

// The API runs on Render's free plan, which may sleep between visitors. Start
// its wake-up request while the visitor is reading the landing page instead of
// making the Open demo button pay the entire cold-start cost. This is best
// effort only: opening the demo keeps its own real account/profile requests
// and handles an unavailable backend normally.
void api.health().catch(() => undefined);

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

function showRecovery() {
  dom.accountView.hidden = true;
  dom.recoveryView.hidden = false;
  session.stopRecovery = renderRecovery(dom.recoveryView, {
    profiles: session.profiles,
    currency: session.account.currency,
    onBack: showAccount,
    announce,
  });
  window.scrollTo({ top: 0, behavior: reducedMotion.matches ? 'auto' : 'smooth' });
  announce('Opening the recovery console. Stage one sweeps your flights for a disruption.');
}

function enterApp() {
  dom.homeView.classList.remove('is-active');
  dom.appView.classList.add('is-active');
  dom.appView.setAttribute('aria-hidden', 'false');
  dom.memberChip.hidden = false;
  dom.memberChip.textContent = `${session.account.member.name} · ${session.account.member.tier}`;

  renderAccount(dom.accountView, {
    data: session.account,
    onCheckFlights: showRecovery,
    announce,
  });

  dom.accountView.hidden = false;
  dom.recoveryView.hidden = true;
  window.scrollTo({ top: 0, behavior: 'auto' });
  window.setTimeout(() => dom.main.focus(), reducedMotion.matches ? 20 : 240);
  announce(`Welcome back, ${session.account.member.first_name}.`);
  // First visit only: explain the shape of the solution before stage 1.
  maybeOpenTutorial();
}

dom.tourButton?.addEventListener('click', (event) => openTutorial(event.currentTarget));

dom.restartButton.addEventListener('click', () => {
  if (session.stopRecovery) {
    session.stopRecovery();
    session.stopRecovery = null;
  }
  // Clear the server-side session too, so the next run starts from a trip with
  // nothing detected rather than inheriting this one's disruption and run.
  api.reset().catch(() => {});
  closeModal(false);
  unmountAll();
  dom.memberChip.hidden = true;
  dom.accountView.innerHTML = '';
  dom.recoveryView.innerHTML = '';
  showHome();
});

document.getElementById('homeLink').addEventListener('click', (event) => {
  event.preventDefault();
  showAccount();
});
