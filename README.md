# super-duper-octo-giggle

news prompt:
---
Combine the images and articles from the provided JSON into a structured JSON output. For each story, include the **title**, **image URL**, and **article text**. Ensure the output is clean, concise, and ready for use in a news application or website. Use the following format for each story:  

```json
{
  "title": "Story Title",
  "image": "Image URL",
  "article": "Article Text"
}
```  

Organize all stories into a single JSON array under the key `stories`. Do not include alt text or unnecessary details. Focus on pairing the most relevant image with its corresponding article.