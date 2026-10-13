export type Segment = { make: string; model: string };
export type Model = Segment & { sample: number };
export type Car = Segment & { listing_id: string; year: number; price: number | null; mileage: number | null; fuel_type: string | null; transmission: string | null; transmission_source?: 'olx_attribute' | 'olx_website_attribute' | 'ad_title' | null; transmission_checked_at?: string | null; transmission_detail_outcome?: string | null; availability?: string; availability_checked_at?: string | null; availability_last_outcome?: string | null; last_verified_at: string; description: string; listing_url: string };
export type Saved = Car & { status: string; note: string };
export type Filters = { make: string; model: string; year_min: number; year_max: number; days: number; price_max: string; mileage_min: string; mileage_max: string; fuel: string; transmission: string; category: string };
export type Evidence = Segment & { sample_size: number; priced_sample: number; median: number | null; p25: number | null; p75: number | null; first_seen_14d: number; outside_window: number; price_cut_listings: number; newest_observation: string | null; sample_warning: string; year_min: number | null; year_max: number | null };
export type Compared = { as_of: string; segments: Evidence[] };
export type Answer = { answer: string; statements: { text: string; evidence_ref: string }[]; limitations: string[]; next_step: string; evidence: Compared };
export type Overview = { snapshots: number; runs: number; api_listings: number; website_listings?: number; website_collection?: { status: string; finished_at: string | null; search_attempted: number; detail_attempted: number; details_completed: number; errors: number } | null; as_of: string | null; models: Model[]; labels: { recognized: number; needs_review: number; categorized: number } };
export type DiscoveryModel = Evidence & { within_budget: number; categorized: number; categories: string[] | null };
export type Discovery = { items: DiscoveryModel[]; as_of: string | null; coverage: { observed: number; recognized: number; categorized: number } };
export type SavedSearch = { id: string; filters: Filters; baseline: Evidence; current: Evidence; baseline_as_of: string; as_of: string; new_data: boolean };
export const initial: Filters = { make: '', model: '', year_min: 2000, year_max: new Date().getFullYear(), days: 30, price_max: '', mileage_min: '', mileage_max: '', fuel: '', transmission: '', category: '' };
export const params = (f: Filters) => { const p = new URLSearchParams(); Object.entries(f).forEach(([k,v])=>{if(v!=='' && v!=null)p.set(k,String(v));});return p; };
export const bodyFilters = (f: Filters) => ({...f,price_max:f.price_max?Number(f.price_max):null,mileage_min:f.mileage_min?Number(f.mileage_min):null,mileage_max:f.mileage_max?Number(f.mileage_max):null});
export const searchFilters = (f: Filters): Filters => ({...f,price_max:'',mileage_min:f.mileage_min==null?'':String(f.mileage_min),mileage_max:f.mileage_max==null?'':String(f.mileage_max)});
export const filterDescription = (f: Filters) => [
 `${f.year_min}–${f.year_max}`, f.fuel, f.transmission, f.category,
 f.mileage_min?`from ${number(Number(f.mileage_min))} km`:'',
 f.mileage_max?`up to ${number(Number(f.mileage_max))} km`:'', `${f.days} days`,
].filter(Boolean).join(' · ');
export const number = (v: number) => new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 }).format(v);
export const money = (v: number | null) => v == null ? 'Price on request' : `${number(v)} KM`;
export const date = (v: string | null) => v ? new Date(v.endsWith('Z') || v.includes('+') ? v : `${v}Z`).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Europe/Sarajevo' }) : 'Unknown';
export const key = (s: Segment) => `${s.make.toLowerCase()}|${s.model.toLowerCase()}`;
export async function api<T>(path: string, body?: unknown, method = 'GET'): Promise<T> {
 const r = await fetch(`/api/${path}`, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
 if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(typeof e.detail === 'string' ? e.detail : 'Request failed. Check the filters and local database.'); } return r.json();
}

export const transmissionLabel = (c: Car) => c.transmission ? `${c.transmission[0].toUpperCase()}${c.transmission.slice(1)}` : c.transmission_detail_outcome === 'not_specified' ? 'Transmission not specified' : 'Transmission not collected';

export const availabilityLabel = (c: Car) => {
 const labels: Record<string,string> = { listed:'Last seen listed', unavailable_pending:'Unavailable · awaiting recheck', removed_unconfirmed:'Removed · sale unconfirmed', source_reported_ended:'Ad ended · sale unconfirmed', source_reported_sold:'OLX reports sold', unknown:'Availability unverified', unverified:'Availability unverified' };
 return `${labels[c.availability || 'unverified'] || 'Availability unverified'}${c.availability_checked_at ? ` · checked ${date(c.availability_checked_at)}` : ''}${['check_failed','blocked'].includes(c.availability_last_outcome || '') ? ' · latest check failed' : ''}`;
};
