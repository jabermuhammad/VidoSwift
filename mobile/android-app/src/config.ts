// Production API. Set EXPO_PUBLIC_API_BASE_URL at build time to use a local backend.
export const API_BASE_URL = (
  process.env.EXPO_PUBLIC_API_BASE_URL || 'https://vidoswift.onrender.com'
).replace(/\/+$/, '');

export const REQUEST_TIMEOUT_MS = 90000;
