# Hybrid retrieval — case SKU trong catalog nhiều sản phẩm cùng dòng

Golden set 10 câu hỏi (1/category, 5 variant/category gần giống hệt nhau, chỉ khác SKU) — recall@1 = tỷ lệ câu hỏi mà top-1 kết quả đúng chunk_id mong đợi. Xem bench/hybrid_sku_case.py cho chi tiết corpus/query.

| Mode | Recall@1 | Đúng / Tổng |
|---|---:|---:|
| Dense thuần (SemanticOnlyEmbedder) | 0.20 | 2/10 |
| Hybrid (BM25 + dense, RRF k=60) | 0.60 | 6/10 |

| Category | Câu hỏi | Expected id | Dense top-1 | Hybrid top-1 |
|---|---|---|---|---|
| Tai nghe Bluetooth chống ồn | Cho tôi thông tin sản phẩm Tai nghe Bluetooth chống ồn mã TN-102 | `TN-chunk-2` | ❌ `TN-chunk-4` | ✅ `TN-chunk-2` |
| Nồi chiên không dầu | Cho tôi thông tin sản phẩm Nồi chiên không dầu mã NC-102 | `NC-chunk-2` | ❌ `NC-chunk-0` | ❌ `NC-chunk-0` |
| Bàn phím cơ RGB | Cho tôi thông tin sản phẩm Bàn phím cơ RGB mã BP-102 | `BP-chunk-2` | ❌ `BP-chunk-1` | ❌ `BP-chunk-1` |
| Sạc dự phòng 20000mAh | Cho tôi thông tin sản phẩm Sạc dự phòng 20000mAh mã SD-102 | `SD-chunk-2` | ❌ `SD-chunk-4` | ✅ `SD-chunk-2` |
| Loa di động chống nước | Cho tôi thông tin sản phẩm Loa di động chống nước mã LD-102 | `LD-chunk-2` | ❌ `LD-chunk-3` | ❌ `LD-chunk-3` |
| Ấm siêu tốc 1.8L | Cho tôi thông tin sản phẩm Ấm siêu tốc 1.8L mã AS-102 | `AS-chunk-2` | ❌ `AS-chunk-0` | ❌ `AS-chunk-0` |
| Chuột không dây | Cho tôi thông tin sản phẩm Chuột không dây mã CK-102 | `CK-chunk-2` | ❌ `CK-chunk-1` | ✅ `CK-chunk-2` |
| Máy xay sinh tố đa năng | Cho tôi thông tin sản phẩm Máy xay sinh tố đa năng mã MX-102 | `MX-chunk-2` | ❌ `OC-chunk-1` | ✅ `MX-chunk-2` |
| Đèn bàn LED chống cận | Cho tôi thông tin sản phẩm Đèn bàn LED chống cận mã DB-102 | `DB-chunk-2` | ✅ `DB-chunk-2` | ✅ `DB-chunk-2` |
| Ổ cắm điện thông minh wifi | Cho tôi thông tin sản phẩm Ổ cắm điện thông minh wifi mã OC-102 | `OC-chunk-2` | ✅ `OC-chunk-2` | ✅ `OC-chunk-2` |
