const express = require("express");
const { extractUrl } = require("./urlExtractor");

const app = express();
const PORT = process.env.PORT || 8080;

// JSON body parser
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// ─── সাধারণ HTML ফর্ম (ফ্রন্টএন্ড) ───
app.get("/", (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html lang="bn">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>URL Extractor</title>
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
          font-family: 'Segoe UI', Tahoma, sans-serif;
          background: linear-gradient(135deg, #0f0c29, #302b63, #24243e);
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          color: #fff;
        }
        .container {
          background: rgba(255, 255, 255, 0.08);
          backdrop-filter: blur(10px);
          border: 1px solid rgba(255, 255, 255, 0.15);
          border-radius: 16px;
          padding: 40px;
          max-width: 560px;
          width: 90%;
        }
        h1 { text-align: center; margin-bottom: 8px; font-size: 1.8rem; }
        p.subtitle { text-align: center; color: #aaa; margin-bottom: 28px; font-size: 0.95rem; }
        label { display: block; margin-bottom: 6px; font-weight: 600; }
        input[type="text"] {
          width: 100%;
          padding: 12px 16px;
          border: 1px solid rgba(255,255,255,0.25);
          border-radius: 8px;
          background: rgba(255,255,255,0.1);
          color: #fff;
          font-size: 1rem;
          outline: none;
          transition: border-color 0.2s;
        }
        input[type="text"]:focus { border-color: #7c4dff; }
        button {
          margin-top: 16px;
          width: 100%;
          padding: 12px;
          font-size: 1rem;
          font-weight: 700;
          border: none;
          border-radius: 8px;
          background: linear-gradient(90deg, #7c4dff, #448aff);
          color: #fff;
          cursor: pointer;
          transition: opacity 0.2s;
        }
        button:hover { opacity: 0.9; }
        .result-box {
          margin-top: 24px;
          padding: 16px;
          border-radius: 8px;
          background: rgba(0,0,0,0.3);
          word-break: break-all;
          display: none;
        }
        .result-box.show { display: block; }
        .result-box h3 { margin-bottom: 8px; color: #80cbc4; }
        .chain { margin-top: 12px; }
        .chain-item {
          padding: 6px 0;
          border-bottom: 1px solid rgba(255,255,255,0.08);
          font-size: 0.9rem;
          color: #ccc;
        }
        .chain-item:last-child { border-bottom: none; }
        .chain-item span.step { color: #7c4dff; font-weight: 700; margin-right: 6px; }
        .final-url { color: #69f0ae; font-weight: 700; font-size: 1.05rem; }
        .error { color: #ff5252; }
        .loader { display: none; text-align: center; margin-top: 16px; color: #aaa; }
        .loader.show { display: block; }
      </style>
    </head>
    <body>
      <div class="container">
        <h1>🔗 URL Extractor</h1>
        <p class="subtitle">শর্ট / রিডাইরেক্ট URL থেকে আসল URL বের করুন</p>

        <label for="url">URL দিন:</label>
        <input type="text" id="url" placeholder="https://bit.ly/example" />
        <button onclick="extract()">এক্সট্রাক্ট করুন</button>

        <div class="loader" id="loader">⏳ অনুসন্ধান চলছে...</div>

        <div class="result-box" id="result"></div>
      </div>

      <script>
        async function extract() {
          const url = document.getElementById("url").value.trim();
          const resultBox = document.getElementById("result");
          const loader = document.getElementById("loader");

          if (!url) { alert("অনুগ্রহ করে একটি URL দিন"); return; }

          resultBox.className = "result-box";
          loader.className = "loader show";

          try {
            const res = await fetch("/api/extract", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ url }),
            });
            const data = await res.json();
            loader.className = "loader";

            if (!data.success) {
              resultBox.innerHTML = '<h3 class="error">❌ ত্রুটি</h3><p>' + data.error + '</p>';
              resultBox.className = "result-box show";
              return;
            }

            let chainHtml = "";
            data.chain.forEach((u, i) => {
              chainHtml += '<div class="chain-item"><span class="step">#' + (i + 1) + '</span>' + u + '</div>';
            });

            resultBox.innerHTML =
              '<h3>✅ আসল URL পাওয়া গেছে</h3>' +
              '<p class="final-url">' + data.finalUrl + '</p>' +
              '<div class="chain"><h3 style="margin-top:12px;">📍 রিডাইরেক্ট চেইন (' + data.totalRedirects + ' টি)</h3>' +
              chainHtml + '</div>';
            resultBox.className = "result-box show";
          } catch (err) {
            loader.className = "loader";
            resultBox.innerHTML = '<h3 class="error">❌ ত্রুটি</h3><p>' + err.message + '</p>';
            resultBox.className = "result-box show";
          }
        }

        // Enter key support
        document.getElementById("url").addEventListener("keydown", (e) => {
          if (e.key === "Enter") extract();
        });
      </script>
    </body>
    </html>
  `);
});

// ─── API Endpoint ───
app.post("/api/extract", async (req, res) => {
  const { url } = req.body;

  if (!url) {
    return res.status(400).json({ success: false, error: "URL প্রদান করা হয়নি" });
  }

  try {
    const result = await extractUrl(url);
    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ─── GET API (query param দিয়েও ব্যবহার করা যায়) ───
app.get("/api/extract", async (req, res) => {
  const { url } = req.query;

  if (!url) {
    return res.status(400).json({
      success: false,
      error: "URL প্রদান করা হয়নি। ব্যবহার: /api/extract?url=https://bit.ly/example",
    });
  }

  try {
    const result = await extractUrl(url);
    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`🚀 URL Extractor চালু আছে: http://localhost:${PORT}`);
});
