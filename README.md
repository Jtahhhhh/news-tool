# news-tool


## Groq / GPT-OSS-120B

Xem [hướng dẫn cấu hình và thêm key Groq](docs/HUONG-DAN-GROQ.md), [audit LLM](docs/AUDIT-LLM-GROQ.md) và [kết quả kiểm thử](docs/GROQ-VALIDATION.md).

Đặt `LLM_PROVIDER=groq`, `GROQ_MODEL=openai/gpt-oss-120b`, key trong `.env.credentials`; dùng `env:GROQ_API_KEY` trên giao diện. Policy đã lưu cần cập nhật tại `/llm`. Worker LLM tách khỏi crawl, nguồn được cố định, đầu ra kiểm tra grounding và chỉ repair một lần.
