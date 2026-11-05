// PyPI downloads excluding mirrors, for the hero stat. pypistats doesn't allow browser
// requests and keeps 180 days of data, so this reads it server-side and caches the sum.
module.exports = async function handler(req, res) {
  try {
    const r = await fetch("https://pypistats.org/api/packages/contribos/overall?mirrors=false");
    if (!r.ok) throw new Error(`pypistats returned ${r.status}`);
    const { data } = await r.json();
    const downloads = data
      .filter((d) => d.category === "without_mirrors")
      .reduce((sum, d) => sum + d.downloads, 0);
    res.setHeader("Cache-Control", "public, s-maxage=43200, stale-while-revalidate=86400");
    res.status(200).json({ downloads });
  } catch {
    res.setHeader("Cache-Control", "no-store");
    res.status(502).json({ error: "download count unavailable" });
  }
};
