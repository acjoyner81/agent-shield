import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';

import { Docs } from './docs';
import { HelpCorpus } from '../../../core/services/help-content.service';

const SECTIONS = [
  {
    source_id: 'section:api-keys',
    section_id: 'api-keys',
    document_id: 'getting-started',
    title: 'Get started with AgentShield',
    heading: 'API keys',
    body: 'Create an API key.',
    blocks: [
      { type: 'paragraph' as const, text: 'Create an API key.', parts: [{ text: 'Create an API key.' }] },
    ],
  },
  {
    source_id: 'section:troubleshooting',
    section_id: 'troubleshooting',
    document_id: 'getting-started',
    title: 'Get started with AgentShield',
    heading: 'Troubleshooting',
    body: 'When something fails.',
    blocks: [
      { type: 'paragraph' as const, text: 'When something fails.', parts: [{ text: 'When something fails.' }] },
    ],
  },
];

const CORPUS: HelpCorpus = {
  documents: [
    { document_id: 'getting-started', title: 'Get started with AgentShield', sections: SECTIONS },
  ],
  sections: SECTIONS,
  suggestions: [],
};

describe('Help center', () => {
  let http: HttpTestingController;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [Docs],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
    http = TestBed.inject(HttpTestingController);
  });

  const render = () => {
    const fixture = TestBed.createComponent(Docs);
    fixture.detectChanges();
    http.expectOne('/help-center-content.json').flush(CORPUS);
    fixture.detectChanges();
    return fixture;
  };

  it('should create', () => {
    expect(render().componentInstance).toBeTruthy();
  });

  it('renders a section navigation whose links point at each section', () => {
    const el = render().nativeElement as HTMLElement;
    const nav = el.querySelector('nav.docs-nav');
    expect(nav).toBeTruthy();
    const links = Array.from(nav!.querySelectorAll('a'));
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['#api-keys', '#troubleshooting']);
    expect(links.map((a) => a.textContent?.trim())).toEqual(['API keys', 'Troubleshooting']);
  });

  it('makes every section heading an anchor link to its own section', () => {
    const el = render().nativeElement as HTMLElement;
    const headingLinks = Array.from(el.querySelectorAll('.docs-section h3 a'));
    expect(headingLinks.length).toBe(2);
    for (const link of headingLinks) {
      const href = link.getAttribute('href') ?? '';
      expect(href.startsWith('#')).toBe(true);
      expect(el.querySelector(href)).toBeTruthy();
    }
  });

  it('renders each section\'s blocks, not just its heading', () => {
    // The gap that shipped empty sections: the nav and anchors resolved, but
    // `documents[].sections[].blocks` was never serialized, so every section
    // rendered a heading with no body. A test that only checks links passes on
    // that. This asserts the paragraph text actually reaches the DOM.
    const el = render().nativeElement as HTMLElement;
    const sections = Array.from(el.querySelectorAll('.docs-section'));
    expect(sections.length).toBe(2);
    expect(sections[0].querySelector('p')?.textContent).toContain('Create an API key.');
    expect(sections[1].querySelector('p')?.textContent).toContain('When something fails.');
  });

  it('renders /api/ links as plain href and supports list item links', () => {
    const corpus: HelpCorpus = {
      documents: [
        {
          document_id: 'getting-started',
          title: 'Get started with AgentShield',
          sections: [
            {
              source_id: 'section:first-request',
              section_id: 'first-request',
              document_id: 'getting-started',
              title: 'Get started with AgentShield',
              heading: 'Make your first request',
              body: 'Try console',
              blocks: [
                {
                  type: 'paragraph' as const,
                  text: 'Try the console',
                  parts: [
                    { text: 'Try the ', url: undefined },
                    { text: 'API console', url: '/api/docs' },
                  ],
                },
                {
                  type: 'list' as const,
                  text: '',
                  items: ['Go to [API keys](/keys)', 'Open [API console](/api/docs)'],
                },
              ],
            },
          ],
        },
      ],
      sections: [],
      suggestions: [],
    };
    const fixture = TestBed.createComponent(Docs);
    fixture.detectChanges();
    http.expectOne('/help-center-content.json').flush(corpus);
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    const apiLinks = Array.from(el.querySelectorAll('a')).filter((a) => a.getAttribute('href') === '/api/docs');
    expect(apiLinks.length).toBeGreaterThanOrEqual(2);
    for (const link of apiLinks) {
      expect(link.getAttribute('routerLink')).toBeNull();
    }
    const keysLink = Array.from(el.querySelectorAll('a')).find((a) => a.getAttribute('href') === '/keys' || a.getAttribute('ng-reflect-router-link') === '/keys' || a.textContent?.includes('API keys'));
    expect(keysLink).toBeTruthy();
  });
});
