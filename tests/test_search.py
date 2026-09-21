import httpx


async def test_search_reads_the_top_pages_including_a_pdf_and_skips_failures():
    from bot.search import BraveSearch, html_to_text
    calls = []
    def handler(req):
        calls.append(str(req.url))
        if "search.brave.com" in str(req.url):
            return httpx.Response(200, json={"web": {"results": [
                {"title": "Nutritionix", "url": "https://n.example/menu", "description": "menu"},
                {"title": "Guide", "url": "https://z.example/guide.pdf", "description": "pdf"},
                {"title": "Dead", "url": "https://dead.example/", "description": "x"},
                {"title": "Fourth", "url": "https://four.example/", "description": "never read"},
            ]}})
        if "n.example" in str(req.url):
            return httpx.Response(200, text="<html><head><script>x()</script><style>a{}</style></head><body><nav>menu</nav><h1>Great 8</h1><p>1,240 calories &amp; 58g protein</p></body></html>", headers={"content-type": "text/html"})
        if "guide.pdf" in str(req.url):
            return httpx.Response(200, content=b"%PDF-1.4 broken", headers={"content-type": "application/pdf"})
        return httpx.Response(503)
    s = BraveSearch("k", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    text = await s.search("zaxbys great 8 calories")
    assert "Page contents:" in text and "=== Nutritionix (https://n.example/menu) ===" in text
    assert "Great 8\n1,240 calories & 58g protein" in text and "x()" not in text and "menu\n" not in text.split("===")[1]
    assert not any("four.example" in c for c in calls)
    assert html_to_text("<p>a</p><br>b <b>c</b>") == "a\nb c"
