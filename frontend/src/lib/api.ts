import axios from "axios";

const API_BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

const client = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
});

// --- Types mirroring the FastAPI response models (main.py) ---

export interface DashboardFilters {
  timeframe_days: number;
  max_price: number;
  price_min: number;
  min_listings: number;
  year_min: number;
  year_max: number;
  transmission: string;
}

export interface OpportunityCard {
  type: "hot_flip" | "overpriced" | "rising_demand";
  title: string;
  subtitle: string;
  model: string | null;
  days: number | null;
  color: string;
}

export interface PriceBracketModel {
  rank: number;
  model: string;
  days: number;
  sample_url?: string | null;
  sample_price?: number | null;
}

export interface PriceBracket {
  bracket_name: string;
  models: PriceBracketModel[];
  total_cars: number;
  total_models: number;
}

export interface FastestSellingModel {
  model: string;
  year: number;
  avg_days_on_market: number;
  avg_price: number;
  demand_level: string;
}

export interface TrendingModel {
  model: string;
  trend_text: string;
  trend_type: "views_up" | "days_down" | "general";
}

export interface PopularModel {
  rank: number;
  model: string;
  listings_count: number;
  total_views: number;
  avg_views: number;
  avg_price: number;
  avg_days_on_market: number;
  search_url: string;
}

export interface ScatterPoint {
  price: number;
  days_on_market: number;
  mileage_bracket: string;
  model: string;
  views: number;
}

export interface DashboardStats {
  total_listings: number;
  timeframe_days: number;
  max_price: number;
  models_analyzed: number;
  last_updated: string;
}

export interface DashboardData {
  opportunities: OpportunityCard[];
  price_brackets: PriceBracket[];
  fastest_selling: FastestSellingModel[];
  trending_models: TrendingModel[];
  popular_models: PopularModel[];
  scatter_data: ScatterPoint[];
  stats: DashboardStats;
}

export interface CarListing {
  listing_id: string;
  make: string;
  model: string;
  year: number | null;
  price: number | null;
  mileage: number | null;
  views: number | null;
  posted_date: string | null;
  location: string | null;
  fuel_type: string | null;
  transmission: string | null;
}

export interface RefreshResponse {
  status: string;
  message: string;
  started_at: string;
}

export interface LastUpdateResponse {
  last_update: string | null;
  total_listings: number;
  new_today: number;
}

// --- API calls ---

export const healthCheck = async (): Promise<{ status: string; timestamp: string }> => {
  const response = await client.get("/health");
  return response.data;
};

export const getDashboardData = async (
  filters: Partial<DashboardFilters> = {}
): Promise<DashboardData> => {
  const response = await client.get("/dashboard", { params: filters });
  return response.data;
};

export const getListings = async (
  filters: Partial<DashboardFilters & { limit: number }> = {}
): Promise<CarListing[]> => {
  const response = await client.get("/listings", { params: filters });
  return response.data;
};

export const refreshData = async (): Promise<RefreshResponse> => {
  const response = await client.post("/refresh-data");
  return response.data;
};

export const getLastUpdate = async (): Promise<LastUpdateResponse> => {
  const response = await client.get("/last-update");
  return response.data;
};
