I spent the last few sessions building a local web research MCP server for LM Studio, mostly through what people have started calling "vibe coding."

The interesting part was not just moving fast. It was the loop:

- describe the behavior I wanted
- run the tool
- hit real edge cases
- tighten the implementation
- add tests
- repeat

The result is a small MCP server that gives local models a practical research workflow:

- live web search only, no stale persistent search index
- page/PDF reading with citation-ready evidence
- optional Playwright rendering for JavaScript-heavy pages
- structured captcha/blocked-page detection
- safe source recovery through print, AMP, PDF, RSS, feed, and sitemap alternatives
- manual visit links when a page requires human access
- tests around the behavior so the tool does not drift

What I liked about this build was how quickly the product shape emerged from real friction. At first, cached results were accidentally winning over live search. That turned into a live-only search policy. Then captcha failures were too vague. That became structured blocked-source reporting. Then blocked pages needed a user path forward. That became manual visit links and source recovery.

That, to me, is the best version of vibe coding: not "let the AI randomly write code," but using AI as a fast engineering partner while keeping the human judgment loop tight.

The repo is designed for local-first use with LM Studio and MCP clients. No paid search API required, no captcha bypassing, no permanent RAG index. Just a focused research tool that tries to be honest about what it can and cannot access.

I will share the GitHub/Hugging Face links once the public repo is cleaned and mirrored.

#AI #MCP #LMStudio #OpenSource #VibeCoding #LocalAI #Python
