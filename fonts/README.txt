Garamond font deployment
========================

This application intentionally does not bundle or redistribute proprietary font files.

For exact Garamond PDF rendering, place properly licensed Garamond TTF/OTF files in
this folder through your private deployment process, then redeploy Railway.

The PDF converter now verifies that fontconfig resolves the family name exactly as
"Garamond". If it does not, conversion stops instead of substituting EB Garamond,
Noto Serif, Liberation Serif, or another font.
