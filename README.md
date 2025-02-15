# super-duper-octo-giggle

news prompt:
**DeepSeek
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

---
**Gemini free API 
---
Create concise and informative summaries for the following news stories, using both the provided "article" text and the "alt" text from the associated "image" to create a more complete description. Aim for summaries that are a few sentences long, capturing the main points of the story and adding context from the image description where relevant.  Format the output as a JSON array of objects, where each object has a "title", "image", and "summary" key.  Focus on news articles; exclude non-article content like calls to action or navigation elements