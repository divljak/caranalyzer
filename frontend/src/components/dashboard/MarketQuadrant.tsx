import React from 'react';
import {
  Box,
  Grid,
  GridItem,
  Heading,
  Text,
  VStack,
} from '@chakra-ui/react';
import {
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type { ScatterPoint, TrendingModel } from '../../lib/api';

interface TrendingItemProps {
  model: TrendingModel;
}

const TrendingItem = ({ model }: TrendingItemProps) => {
  const getTrendColor = (type: string) => {
    switch (type) {
      case 'views_up':
        return 'success.500';
      case 'days_down':
        return 'brand.500';
      default:
        return 'gray.500';
    }
  };

  return (
    <Box
      bg="white"
      borderRadius="lg"
      p={4}
      mb={3}
      borderLeft="3px solid"
      borderLeftColor={getTrendColor(model.trend_type)}
    >
      <VStack align="start" spacing={1}>
        <Text fontWeight="semibold" color="gray.700" fontSize="md">
          {model.model}
        </Text>
        <Text fontSize="sm" color={getTrendColor(model.trend_type)} fontWeight="medium">
          {model.trend_text}
        </Text>
      </VStack>
    </Box>
  );
};

interface MarketQuadrantProps {
  scatterData: ScatterPoint[];
  trendingModels: TrendingModel[];
}

const MarketQuadrant = ({ scatterData, trendingModels }: MarketQuadrantProps) => {
  // Process scatter data for recharts
  const chartData = scatterData.map((point) => ({
    x: point.price,
    y: point.days_on_market,
    z: point.views,
    model: point.model,
    mileage: point.mileage_bracket,
  }));

  // Calculate averages for reference lines
  const avgPrice = scatterData.reduce((sum, point) => sum + point.price, 0) / scatterData.length;
  const avgDays = scatterData.reduce((sum, point) => sum + point.days_on_market, 0) / scatterData.length;

  // Color mapping for mileage brackets
  const getMileageColor = (mileage: string) => {
    switch (mileage) {
      case '<50k km':
        return '#4f46e5';
      case '50k-100k km':
        return '#059669';
      case '100k-150k km':
        return '#d97706';
      case '150k+ km':
        return '#dc2626';
      default:
        return '#6b7280';
    }
  };

  interface CustomTooltipProps {
    active?: boolean;
    payload?: Array<{
      payload: {
        x: number;
        y: number;
        z: number;
        model: string;
        mileage: string;
      };
    }>;
  }

  const CustomTooltip = ({ active, payload }: CustomTooltipProps) => {
    if (active && payload && payload.length) {
      const data = payload[0].payload;
      return (
        <Box bg="white" p={3} borderRadius="md" boxShadow="lg" border="1px solid" borderColor="gray.200">
          <Text fontWeight="semibold">{data.model}</Text>
          <Text fontSize="sm">Price: {data.x.toLocaleString()} KM</Text>
          <Text fontSize="sm">Days on Market: {data.y}</Text>
          <Text fontSize="sm">Views: {data.z}</Text>
          <Text fontSize="sm">Mileage: {data.mileage}</Text>
        </Box>
      );
    }
    return null;
  };

  return (
    <Box mb={8}>
      <Grid templateColumns={{ base: '1fr', lg: '2fr 1fr' }} gap={6}>
        {/* Market Quadrant Analysis */}
        <GridItem>
          <Box bg="white" borderRadius="xl" boxShadow="lg" p={6}>
            <Heading size="lg" mb={4} color="gray.800">
              Market Quadrant Analysis
            </Heading>
            <Box h="320px">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    type="number"
                    dataKey="x"
                    name="price"
                    unit=" KM"
                    tickFormatter={(value) => `${(value / 1000).toFixed(0)}k`}
                  />
                  <YAxis
                    type="number"
                    dataKey="y"
                    name="days"
                    unit=" days"
                  />
                  <Tooltip content={<CustomTooltip />} />
                  <ReferenceLine
                    x={avgPrice}
                    stroke="red"
                    strokeDasharray="5 5"
                    opacity={0.7}
                  />
                  <ReferenceLine
                    y={avgDays}
                    stroke="red"
                    strokeDasharray="5 5"
                    opacity={0.7}
                  />
                  {/* Group data by mileage bracket for different colors */}
                  {['<50k km', '50k-100k km', '100k-150k km', '150k+ km'].map((mileage) => (
                    <Scatter
                      key={mileage}
                      name={mileage}
                      data={chartData.filter((point) => point.mileage === mileage)}
                      fill={getMileageColor(mileage)}
                    />
                  ))}
                </ScatterChart>
              </ResponsiveContainer>
            </Box>
          </Box>
        </GridItem>

        {/* Trending Up */}
        <GridItem>
          <Box bg="white" borderRadius="xl" boxShadow="lg" p={6}>
            <Heading size="lg" mb={4} color="gray.800">
              📈 Trending Up
            </Heading>
            <VStack align="stretch" spacing={0}>
              {trendingModels && trendingModels.length > 0 ? (
                trendingModels.map((model, index) => (
                  <TrendingItem key={index} model={model} />
                ))
              ) : (
                <Text color="gray.500" fontSize="sm">
                  Monitoring market trends...
                </Text>
              )}
            </VStack>
          </Box>
        </GridItem>
      </Grid>
    </Box>
  );
};

export default MarketQuadrant;