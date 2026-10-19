// Read-only backend status check. Never sends printer controls.
fetch('http://print-view.local/api/status').then(async response => {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const status = await response.json();
  if (!status.connected) throw new Error('Printer unavailable');
  console.log(JSON.stringify(status, null, 2));
}).catch(error => { console.error(error.message); process.exitCode = 1; });
