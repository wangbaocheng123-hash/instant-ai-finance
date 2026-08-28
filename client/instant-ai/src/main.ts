import './instant-ai/styles.css';
import { InstantFinanceApp } from './instant-ai/InstantFinanceApp';

if (new URLSearchParams(window.location.search).has('mobile-preview')) {
  document.title = '即时 AI · 手机预览';
}

const root = document.querySelector<HTMLElement>('#app');

if (!root) {
  throw new Error('Instant AI root element is missing');
}

const app = new InstantFinanceApp(root);
void app.start();

if ('serviceWorker' in navigator && (window.isSecureContext || location.hostname === '127.0.0.1' || location.hostname === 'localhost')) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js');
  });
}
