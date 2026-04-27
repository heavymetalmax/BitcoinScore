// Placeholder mapping functions: implement domain logic later
function mapNuplToSlider(nupl){
  // nupl expected in percent (-50..100)
  if (nupl === null || nupl === undefined) return 50;
  const v = Math.max(-50, Math.min(100, nupl));
  return Math.round(((v + 50) / 150) * 100);
}

function mapMvrvToSlider(mvrv){
  if (mvrv === null || mvrv === undefined) return 50;
  const v = Math.max(-2, Math.min(10, mvrv));
  return Math.round(((v + 2) / 12) * 100);
}

function mapProfitToSlider(sopr){
  // SOPR (adjusted): -0.05=capitulation (bullish 0) … +0.10=distribution (bearish 100)
  if (sopr === null || sopr === undefined) return 50;
  const v = Math.max(-0.05, Math.min(0.10, sopr));
  return Math.round(((v + 0.05) / 0.15) * 100);
}

function mapGeoriskToSlider(gpr){
  // 0 = absolute stability (100 bullish), ~350 = extreme risk (0 bearish)
  if (gpr === null || gpr === undefined) return 50;
  const v = Math.max(0, Math.min(350, gpr));
  return Math.round(((350 - v) / 350) * 100);
}

export { mapNuplToSlider, mapMvrvToSlider, mapProfitToSlider, mapGeoriskToSlider };
