from app_dashboard.content_inventory import overlap_candidates, parse_page, parse_sitemap


def test_parses_namespaced_sitemap_and_content():
    pages=parse_sitemap('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://newcraft.dev/test/</loc><lastmod>2026-08-12</lastmod></url></urlset>')
    assert pages == [{"url":"https://newcraft.dev/test","modified":"2026-08-12"}]
    page=parse_page("<html><head><title>Import books</title></head><body><nav>skip</nav><main><h1>ISBN imports</h1><p>Use an ISBN scanner.</p></main></body></html>",pages[0]["url"])
    assert page["headings"] == ["ISBN imports"]
    assert "skip" not in page["body_text"]


def test_overlap_candidates_are_deterministic(db):
    db.execute("""insert into content_inventory (canonical_url,title,slug,headings,content_digest)
                  values ('https://newcraft.dev/isbn','Import books with ISBN','isbn-book-import', '["ISBN scanner"]','abc')""")
    assert overlap_candidates(db,"ISBN book import")[0]["title"] == "Import books with ISBN"
