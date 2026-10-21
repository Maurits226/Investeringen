// Niet laten inbedden door een andere website (clickjacking). Het dashboard zelf (index.html, zelfde site) mag wel.
// De pagina staat verborgen (zie de <style id="af"> in de kop) tot dit script heeft gecontroleerd wie hem toont.
(function () {
  var ok = false;
  try { ok = window.top === window.self || window.top.location.origin === location.origin; } catch (e) { ok = false; }
  if (ok) { var s = document.getElementById('af'); if (s) s.parentNode.removeChild(s); }
  else { try { window.top.location = location.href; } catch (e) {} }
})();
