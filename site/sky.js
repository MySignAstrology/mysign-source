/* The living sky, shared with the app (app/static/index.html, MySky).
   Loaded synchronously in <head> so the first paint is already the right hour. */
(function(){
  var A = [0, 5, 6.5, 9, 13, 17.5, 19.5, 21.5];          // anchor hours
  var DARK = [
    ['#07091A','#090C1F','#0C1028','#101632'],
    ['#0A0C1E','#0F1228','#171734','#211D40'],
    ['#0E1024','#161733','#221E40','#30264A'],
    ['#0D1022','#131730','#1A203E','#242C4A'],
    ['#0E1124','#151A33','#1D2442','#283250'],
    ['#0E1022','#171631','#241D3E','#33254A'],
    ['#0A0B1C','#111128','#1B1735','#281E3E'],
    ['#07091A','#0A0C20','#0E102A','#141634']];
  var LIGHT = [
    ['#EADDD8','#E2CBC6','#D3B0AE','#C4979B'],
    ['#EEE3E2','#E8D4D2','#E3C0B6','#DDAE9C'],
    ['#F8E7DA','#F6D8C4','#F2C4A8','#ECCFA8'],
    ['#F9F0E6','#F7E6D4','#F3D8BE','#F0CFA8'],
    ['#FAF3EA','#F8EBDB','#F4DFC5','#F2D6B0'],
    ['#F7E4D3','#F2CCB2','#EBB39C','#E8A898'],
    ['#EED9D5','#E5C1BD','#DAA6A8','#CD9198'],
    ['#E7D6D4','#DABEBF','#C9A2A8','#B98A93']];
  function hex(c){ c=parseInt(c.slice(1),16); return [(c>>16)/255,(c>>8&255)/255,(c&255)/255]; }
  function lin(v){ return v<=.04045?v/12.92:Math.pow((v+.055)/1.055,2.4); }
  function gam(v){ v=Math.max(0,Math.min(1,v)); return v<=.0031308?12.92*v:1.055*Math.pow(v,1/2.4)-.055; }
  function toLab(c){ var r=lin(c[0]),g=lin(c[1]),b=lin(c[2]);
    var l=Math.cbrt(.4122214708*r+.5363325363*g+.0514459929*b),
        m=Math.cbrt(.2119034982*r+.6806995451*g+.1073969566*b),
        s=Math.cbrt(.0883024619*r+.2817188376*g+.6299787005*b);
    return [.2104542553*l+.7936177850*m-.0040720468*s, 1.9779984951*l-2.4285922050*m+.4505937099*s,
            .0259040371*l+.7827717662*m-.8086757660*s]; }
  function toRgb(L){ var l=Math.pow(L[0]+.3963377774*L[1]+.2158037573*L[2],3),
        m=Math.pow(L[0]-.1055613458*L[1]-.0638541728*L[2],3),
        s=Math.pow(L[0]-.0894841775*L[1]-1.2914855480*L[2],3);
    return [gam(4.0767416621*l-3.3077115913*m+.2309699292*s), gam(-1.2684380046*l+2.6097574011*m-.3413193965*s),
            gam(-.0041960863*l-.7034186147*m+1.7076147010*s)]; }
  function mix(a,b,t){ var x=toLab(hex(a)),y=toLab(hex(b));
    var c=toRgb([x[0]+(y[0]-x[0])*t, x[1]+(y[1]-x[1])*t, x[2]+(y[2]-x[2])*t]);
    return 'rgb('+c.map(function(v){return Math.round(v*255);}).join(',')+')'; }
  function at(d, dark){
    var h = d.getHours() + d.getMinutes()/60, P = dark ? DARK : LIGHT, i = A.length-1;
    for (var k=0;k<A.length;k++) if (A[k] <= h) i = k;
    var j = (i+1) % A.length, span = ((A[j] - A[i]) + 24) % 24 || 24,
        t = (((h - A[i]) + 24) % 24) / span;
    t = t*t*(3-2*t);                                       // ease, no visible seams
    return P[i].map(function(c,n){ return mix(c, P[j][n], t); });
  }
  function bucket(h){ return h>=5&&h<9?'dawn':h>=9&&h<17?'day':h>=17&&h<21?'dusk':'night'; }
  function apply(d){
    d = d || new Date();
    var dark = true,   // night only: one look at every hour
        s = at(d, dark), r = document.documentElement.style, h = d.getHours()+d.getMinutes()/60;
    for (var n=0;n<4;n++) r.setProperty('--s'+n, s[n]);
    // Sun 06:00–19:00, moon 19:00–06:00, each on an arc over the top of the screen.
    var sun = h>=6 && h<19, t = sun ? (h-6)/13 : (((h-19)+24)%24)/11,
        arc = Math.sin(Math.PI*t);
    r.setProperty('--orb-x', (6 + t*88).toFixed(1)+'%');
    // Kept in the band above the content: it rises at the edge, crests near
    // the top and sets at the far side, but never low enough to sit behind words.
    r.setProperty('--orb-y', (21 - arc*15).toFixed(1)+'%');
    r.setProperty('--orb-o', Math.min(1, arc*2.2).toFixed(2));
    r.setProperty('--star-o', (h>=21.5||h<5) ? 1 : (h>=20.5||h<6) ? .45 : 0);
    document.documentElement.dataset.sky = bucket(d.getHours());
    document.documentElement.dataset.orb = sun ? 'sun' : 'moon';
    var m = document.querySelector('meta[name=theme-color]:not([media])');
    if (m) m.content = s[0];
  }
  window.MySky = {apply: apply, at: at, bucket: bucket};
  apply();
  setInterval(function(){ apply(); }, 60000);
})();
