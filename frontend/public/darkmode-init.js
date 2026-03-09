(function(){
  try {
    var pref = localStorage.getItem('darkreader');
    if (pref === 'on') {
      document.documentElement.classList.add('darkreader-init');
      var s = document.createElement('script');
      s.src = 'https://unpkg.com/darkreader@4.9.120/darkreader.min.js';
      s.onload = function() {
        DarkReader.enable({brightness:100,contrast:90,sepia:0});
        document.documentElement.classList.remove('darkreader-init');
      };
      document.head.appendChild(s);
    }
  } catch (e) {}
})();
