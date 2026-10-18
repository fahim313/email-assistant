import { useEffect, useState } from "react";
import { getHealth, getDatabaseHealth } from "../lib/api";

export default function BackendStatus() {
  const [backendStatus, setBackendStatus] = useState("Checking...");
  const [databaseStatus, setDatabaseStatus] = useState("Checking...");

  useEffect(() => {
    getHealth()
      .then((data) => setBackendStatus(data.status))
      .catch(() => setBackendStatus("Disconnected"));

    getDatabaseHealth()
      .then((data) => setDatabaseStatus(data.status))
      .catch(() => setDatabaseStatus("Disconnected"));
  }, []);

  return (
    <section>
      <h2>System Status</h2>
      <p>Backend API: {backendStatus}</p>
      <p>Database: {databaseStatus}</p>
    </section>
  );
}