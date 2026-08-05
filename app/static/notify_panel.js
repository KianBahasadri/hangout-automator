(function () {
  const enabled = document.getElementById("notify_enabled");
  const sections = document.getElementById("notify-sections");
  const interval = document.getElementById("notify_interval");
  const intervalOpts = document.getElementById("notify-interval-opts");
  const threshold = document.getElementById("notify_threshold");
  const thresholdOpts = document.getElementById("notify-threshold-opts");
  if (!enabled || !sections) return;

  function sync() {
    const on = enabled.checked;
    sections.hidden = !on;
    if (intervalOpts) intervalOpts.hidden = !(on && interval && interval.checked);
    if (thresholdOpts) thresholdOpts.hidden = !(on && threshold && threshold.checked);
  }

  enabled.addEventListener("change", sync);
  if (interval) interval.addEventListener("change", sync);
  if (threshold) threshold.addEventListener("change", sync);
  sync();
})();
