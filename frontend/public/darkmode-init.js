(function () {
  try {
    var pref = localStorage.getItem('darkreader');
    var wantDark = false;
    if (pref === 'on' || pref === 'dark') {
      wantDark = true;
    } else if (pref === 'off' || pref === 'light') {
      wantDark = false;
    } else {
      // 'system', unknown, or unset — follow OS (default)
      wantDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    }
    if (wantDark) {
      document.documentElement.classList.add('darkreader-init');
      var s = document.createElement('script');
      s.src = 'https://unpkg.com/darkreader@4.9.120/darkreader.min.js';
      s.onload = function () {
        DarkReader.enable({ brightness: 150, contrast: 100, sepia: 0 });
        document.documentElement.classList.remove('darkreader-init');
      };
      document.head.appendChild(s);
    }
  } catch (e) {}
})();
