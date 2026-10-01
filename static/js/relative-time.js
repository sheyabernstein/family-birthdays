const formatter = new Intl.RelativeTimeFormat("en", {
  numeric: "auto",
});

function formatRelativeTime(timestamp) {
  const diff = timestamp - Date.now();

  const seconds = diff / 1000;
  const minutes = seconds / 60;
  const hours = minutes / 60;
  const days = hours / 24;

  if (Math.abs(seconds) < 60) {
    return formatter.format(Math.round(seconds), "second");
  }

  if (Math.abs(minutes) < 60) {
    return formatter.format(Math.round(minutes), "minute");
  }

  if (Math.abs(hours) < 24) {
    return formatter.format(Math.round(hours), "hour");
  }

  return formatter.format(Math.round(days), "day");
}

function updateNaturalTimes() {
  document.querySelectorAll(".natural-time").forEach((element) => {
    const timestamp = new Date(element.dataset.timestamp).getTime();

    if (Number.isNaN(timestamp)) {
      return;
    }

    element.textContent = formatRelativeTime(timestamp);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  updateNaturalTimes();

  setInterval(updateNaturalTimes, 60_000);
});
