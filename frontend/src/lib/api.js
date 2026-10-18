const API_BASE_URL = (
  import.meta.env.VITE_API_URL || "/api"
).replace(/\/+$/, "");

export async function apiGet(path) {
  const normalizedPath = path.replace(/^\/+/, "");
  const response = await fetch(
    `${API_BASE_URL}/${normalizedPath}`
  );

  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }

  return response.json();
}

export function getHealth() {
  return apiGet("health");
}

export function getDatabaseHealth() {
  return apiGet("health/db");
}