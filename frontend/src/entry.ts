// Migrate the research surface first; existing management/review routes remain usable.
if (['/', '/agent'].includes(window.location.pathname)) {
  void import('./vue/main');
} else {
  void import('./main');
}
