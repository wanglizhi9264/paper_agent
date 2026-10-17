import MarkdownIt from 'markdown-it';
// Paper/model content is untrusted. Disable HTML, images and navigable links.
const md = new MarkdownIt({ html: false, breaks: true, linkify: false });
md.renderer.rules.image = (tokens, index) => md.utils.escapeHtml(tokens[index]?.content ?? '');
md.renderer.rules.link_open = () => '<span class="paper-link">';
md.renderer.rules.link_close = () => '</span>';
export const renderAnswer = (text: string) => md.render(text);
