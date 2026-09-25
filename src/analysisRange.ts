export function formatRangeTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--:--";
  const rounded = Math.round(seconds * 100) / 100;
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const useHundredths =
    Math.abs(rounded * 10 - Math.round(rounded * 10)) > 1e-6;
  const decimals = useHundredths ? 2 : 1;
  const remainder = (rounded % 60)
    .toFixed(decimals)
    .padStart(decimals + 3, "0");
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${remainder}`
    : `${minutes}:${remainder}`;
}
