/**
 * Grocery & E-Commerce Authenticated Price Tracker Workflow.
 * Author: Sandeep Hipparagi (https://github.com/Sandeep-Hipparagi)
 *
 * Tier 0: Extracts product metadata and historical price telemetry using semantic DOM selectors.
 * Tier 1: Supports staging items into cart without ever initiating checkout/payment flows.
 */

import { GroceryProductSnapshot } from '../types/bridge';

export class GroceryTrackerWorkflow {
  private host: string;

  constructor() {
    this.host = window.location.hostname.toLowerCase();
  }

  public isSupportedPage(): boolean {
    return (
      this.host.includes('amazon.') ||
      this.host.includes('instacart.') ||
      this.host.includes('target.') ||
      this.host.includes('walmart.') ||
      document.querySelector('meta[property*="price"]') !== null ||
      document.querySelector('script[type="application/ld+json"]') !== null
    );
  }

  /**
   * TIER_0_OBSERVE: Scrapes product metadata using a cascade of semantic selectors.
   */
  public extractProductSnapshot(): GroceryProductSnapshot | null {
    // 1. Try schema.org JSON-LD extraction
    const jsonLdSnapshot = this.extractFromJsonLd();
    if (jsonLdSnapshot) return jsonLdSnapshot;

    // 2. Try OpenGraph & Meta tag extraction
    const metaSnapshot = this.extractFromMetaTags();
    if (metaSnapshot) return metaSnapshot;

    // 3. Fallback to portal-specific DOM heuristics
    return this.extractFromDomFallback();
  }

  /**
   * TIER_1_STAGE: Stages item into cart by clicking "Add to Cart" button.
   * Explicitly avoids any checkout, 1-click buy, or payment elements.
   */
  public stageAddToCart(): { success: boolean; message: string } {
    const addToCartSelectors = [
      '#add-to-cart-button',
      'button[name="submit.add-to-cart"]',
      'button[data-testid="add-to-cart"]',
      'button[aria-label*="Add to cart" i]',
      'button[aria-label*="Add to basket" i]',
      'button:has-text("Add to Cart")',
    ];

    for (const selector of addToCartSelectors) {
      const button = document.querySelector(selector) as HTMLButtonElement | null;
      if (button && !button.disabled) {
        // Security check: ensure button is NOT a checkout or immediate purchase trigger
        const btnText = (button.innerText || button.getAttribute('aria-label') || '').toLowerCase();
        if (btnText.includes('buy now') || btnText.includes('checkout') || btnText.includes('purchase')) {
          return {
            success: false,
            message: 'Blocked by Policy Enclave: Button resembles immediate checkout (TIER_2_COMMIT required).'
          };
        }

        button.click();
        return {
          success: true,
          message: 'Item staged into cart (TIER_1_STAGE). Checkout remains locked.'
        };
      }
    }

    return {
      success: false,
      message: 'No safe Add to Cart element found on active tab.'
    };
  }

  // ==========================================================================
  // Selector Strategies
  // ==========================================================================

  private extractFromJsonLd(): GroceryProductSnapshot | null {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    for (const script of Array.from(scripts)) {
      try {
        const json = JSON.parse(script.textContent || '{}');
        const items = Array.isArray(json) ? json : [json];

        for (const item of items) {
          if (item['@type'] === 'Product' || item['@type']?.includes?.('Product')) {
            const title = item.name || '';
            const offers = Array.isArray(item.offers) ? item.offers[0] : item.offers;
            const unitPrice = offers?.price ? parseFloat(offers.price) : null;
            const currency = offers?.priceCurrency || 'USD';
            const inStock = offers?.availability ? String(offers.availability).includes('InStock') : true;
            const sku = item.sku || item.productID || '';

            if (title && unitPrice !== null) {
              return {
                portal: this.host,
                title: title.trim(),
                unit_price: unitPrice,
                currency,
                in_stock: inStock,
                sku: String(sku),
                url: window.location.href,
                can_stage_cart: this.findAddToCartButton() !== null,
                extracted_at: new Date().toISOString()
              };
            }
          }
        }
      } catch (e) {
        // Malformed JSON-LD, proceed to next strategy
      }
    }
    return null;
  }

  private extractFromMetaTags(): GroceryProductSnapshot | null {
    const title =
      document.querySelector('meta[property="og:title"]')?.getAttribute('content') ||
      document.querySelector('meta[name="twitter:title"]')?.getAttribute('content');

    const priceStr =
      document.querySelector('meta[property="product:price:amount"]')?.getAttribute('content') ||
      document.querySelector('meta[property="og:price:amount"]')?.getAttribute('content');

    const currency =
      document.querySelector('meta[property="product:price:currency"]')?.getAttribute('content') ||
      document.querySelector('meta[property="og:price:currency"]')?.getAttribute('content') ||
      'USD';

    if (title && priceStr) {
      const price = parseFloat(priceStr);
      if (!isNaN(price)) {
        return {
          portal: this.host,
          title: title.trim(),
          unit_price: price,
          currency,
          in_stock: true,
          url: window.location.href,
          can_stage_cart: this.findAddToCartButton() !== null,
          extracted_at: new Date().toISOString()
        };
      }
    }
    return null;
  }

  private extractFromDomFallback(): GroceryProductSnapshot | null {
    let title = '';
    let price: number | null = null;
    let currency = 'USD';

    if (this.host.includes('amazon.')) {
      title = document.querySelector('#productTitle')?.textContent?.trim() || '';
      const whole = document.querySelector('.a-price-whole')?.textContent?.trim() || '';
      const fraction = document.querySelector('.a-price-fraction')?.textContent?.trim() || '00';
      const symbol = document.querySelector('.a-price-symbol')?.textContent?.trim() || '$';

      if (whole) {
        price = parseFloat(`${whole.replace(/[^0-9]/g, '')}.${fraction}`);
        currency = symbol === '₹' ? 'INR' : symbol === '£' ? 'GBP' : symbol === '€' ? 'EUR' : 'USD';
      }
    } else if (this.host.includes('instacart.')) {
      title = document.querySelector('h1')?.textContent?.trim() || '';
      const priceText = document.querySelector('[data-testid="item-price"]')?.textContent?.trim() || '';
      const match = priceText.match(/[\$£€₹]?\s*([0-9]+\.?[0-9]*)/);
      if (match) price = parseFloat(match[1]);
    } else {
      title = document.title || '';
      const genericPriceEl = document.querySelector('[itemprop="price"], .price, .product-price');
      if (genericPriceEl?.textContent) {
        const match = genericPriceEl.textContent.match(/([0-9]+\.?[0-9]*)/);
        if (match) price = parseFloat(match[1]);
      }
    }

    if (!title && !price) return null;

    return {
      portal: this.host,
      title: title || document.title,
      unit_price: price,
      currency,
      in_stock: !document.body.innerText.toLowerCase().includes('currently unavailable'),
      url: window.location.href,
      can_stage_cart: this.findAddToCartButton() !== null,
      extracted_at: new Date().toISOString()
    };
  }

  private findAddToCartButton(): HTMLElement | null {
    return document.querySelector('#add-to-cart-button, button[name*="add-to-cart" i], button[data-testid="add-to-cart"]');
  }
}

// Make globally accessible for plugin registry
(window as any).__groceryWorkflow = new GroceryTrackerWorkflow();
