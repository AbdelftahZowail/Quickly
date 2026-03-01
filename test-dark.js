const DarkReader = require('./frontend/node_modules/darkreader');
console.log('isEnabled', DarkReader.isEnabled());
DarkReader.enable({brightness:150,contrast:100,sepia:0});
setTimeout(async () => {
  const css = await DarkReader.exportGeneratedCSS();
  console.log('css length', css.length);
  DarkReader.disable();
}, 1000);
